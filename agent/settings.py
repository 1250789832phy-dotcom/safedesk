import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from agent.planner import MockPlanner, OpenAIResponsesPlanner


SETTINGS_PATH = Path(__file__).resolve().parents[1] / "data" / "model-settings.json"
ProtocolName = Literal["responses", "chat_completions"]


class ModelCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1, max_length=2000)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), max_length=4096)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        value = value.strip()
        parsed = urlsplit(value)
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(char.isspace() or ord(char) < 32 for char in value)
            or "\\" in value
        ):
            raise ValueError("Invalid base URL")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("HTTPS is required except for a local model server")
        parsed.port
        path = parsed.path.rstrip("/")
        if path.endswith(("/chat/completions", "/responses")):
            raise ValueError("Use the base URL, not a complete endpoint")
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


class ModelConfiguration(ModelCredentials):
    model: str = Field(min_length=1, max_length=200)
    protocol: ProtocolName = "chat_completions"

    @field_validator("model")
    @classmethod
    def validate_model(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("A model name is required")
        return value


class StoredConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    mode: Literal["mock", "api"] = "mock"
    base_url: str = ""
    model: str = ""
    protocol: ProtocolName = "chat_completions"
    encrypted_api_key: str = ""


class DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def protect_key(value: bytes, *, decrypt=False) -> bytes:
    if os.name != "nt":
        raise OSError("Encrypted persistence requires Windows DPAPI")
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source = DataBlob(len(value), buffer)
    destination = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not operation(
        ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)
    ):
        raise OSError("Windows could not protect or restore the API key")
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(destination.pbData)


def build_client(configuration: ModelCredentials):
    import httpx
    from openai import OpenAI

    return OpenAI(
        api_key=configuration.api_key.get_secret_value(),
        base_url=configuration.base_url,
        timeout=20,
        max_retries=0,
        http_client=httpx.Client(follow_redirects=False, trust_env=False, timeout=20),
    )


def build_planner(configuration: ModelConfiguration):
    return OpenAIResponsesPlanner(
        build_client(configuration),
        configuration.model,
        protocol=configuration.protocol,
        api_key=configuration.api_key.get_secret_value(),
    )


class ModelSettings:
    def __init__(self, service, path=SETTINGS_PATH, factory=None, load=True):
        self.service = service
        self.path = Path(path)
        self.factory = factory or build_planner
        self.lock = RLock()
        self.configuration = None
        self.stored = StoredConfiguration()
        self.mode = "mock" if isinstance(service.planner, MockPlanner) else "environment"
        self.load_error = False
        if load:
            self._load()

    @staticmethod
    def _close_planner(planner):
        client = getattr(planner, "client", None)
        if client is not None:
            try:
                client.close()
            except Exception:
                # Cleanup must not turn a committed configuration into an error.
                pass

    def status(self):
        with self.lock:
            return {
                "mode": self.mode,
                "base_url": self.stored.base_url,
                "model": self.stored.model,
                "protocol": self.stored.protocol,
                "has_api_key": self.configuration is not None,
                "load_error": self.load_error,
                "storage": "windows-dpapi",
            }

    def _load(self):
        if not self.path.exists():
            return
        try:
            if self.path.is_symlink():
                raise ValueError("Symbolic settings path")
            stored = StoredConfiguration.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
            configuration = None
            if stored.encrypted_api_key:
                key = protect_key(
                    base64.b64decode(stored.encrypted_api_key, validate=True),
                    decrypt=True,
                ).decode("utf-8")
                configuration = ModelConfiguration(
                    base_url=stored.base_url, model=stored.model,
                    protocol=stored.protocol, api_key=key,
                )
                if not key:
                    raise ValueError("Empty saved key")
            if stored.mode == "api" and configuration is None:
                raise ValueError("Missing saved key")
            planner = self.factory(configuration) if stored.mode == "api" else MockPlanner()
            self.stored = stored
            self.configuration = configuration
            self.mode = stored.mode
            self._close_planner(self.service.planner)
            self.service.planner = planner
        except Exception:
            self.load_error = True
            self._close_planner(self.service.planner)
            self.service.planner = MockPlanner()
            self.mode = "mock"

    def resolve(self, configuration):
        if configuration.api_key.get_secret_value().strip():
            return configuration
        if self.configuration and configuration.base_url == self.configuration.base_url:
            return configuration.model_copy(update={"api_key": self.configuration.api_key})
        raise ValueError("Changing URL or first setup requires a new API key")

    def _write(self, stored):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise OSError("Symbolic settings paths are not allowed")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".model-settings-", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(stored.model_dump(), stream, ensure_ascii=True, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self, configuration):
        with self.lock:
            configuration = self.resolve(configuration)
            planner = self.factory(configuration)
            try:
                stored = StoredConfiguration(
                    mode="api", base_url=configuration.base_url, model=configuration.model,
                    protocol=configuration.protocol,
                    encrypted_api_key=base64.b64encode(
                        protect_key(configuration.api_key.get_secret_value().encode("utf-8"))
                    ).decode("ascii"),
                )
                self._write(stored)
            except Exception:
                self._close_planner(planner)
                raise
            previous = self.service.planner
            self.stored, self.configuration = stored, configuration
            self.service.planner, self.mode = planner, "api"
            self._close_planner(previous)
            self.load_error = False
            return self.status()

    def use_mock(self, *, forget=False):
        with self.lock:
            stored = (
                StoredConfiguration()
                if forget else self.stored.model_copy(update={"mode": "mock"})
            )
            self._write(stored)
            self.stored = stored
            if forget:
                self.configuration = None
            previous = self.service.planner
            self.service.planner, self.mode = MockPlanner(), "mock"
            self._close_planner(previous)
            self.load_error = False
            return self.status()

    def probe(self, configuration):
        with self.lock:
            configuration = self.resolve(configuration)
        planner = self.factory(configuration)
        try:
            planner.check_connection()
        finally:
            self._close_planner(planner)

    def discover_models(self, credentials: ModelCredentials):
        with self.lock:
            credentials = self.resolve(credentials)
        with build_client(credentials) as client:
            catalog = client.models.list()
            if not isinstance(catalog.data, list):
                raise RuntimeError("Invalid upstream model catalog")
            key = credentials.api_key.get_secret_value()
            models = set()
            for entry in catalog.data:
                model_id = getattr(entry, "id", None)
                if not isinstance(model_id, str):
                    continue
                model_id = model_id.strip()
                if (
                    not model_id or len(model_id) > 200
                    or any(ord(char) < 32 for char in model_id)
                    or key in model_id
                ):
                    continue
                models.add(model_id)
            ordered = sorted(models, key=lambda value: (value.casefold(), value))
            return {"models": ordered[:2000], "truncated": len(ordered) > 2000}
