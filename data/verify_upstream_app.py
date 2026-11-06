from pathlib import Path
import tempfile

from fastapi import Request
from fastapi.responses import JSONResponse

from agent.service import AgentService
from apps.api.main import create_app


temporary = tempfile.TemporaryDirectory(prefix="safedesk-model-discovery-")
app = create_app(
    AgentService(), settings_path=Path(temporary.name) / "settings.json"
)
observed = []


@app.get("/fixture/v1/models")
def fixture_models(request: Request):
    authorization = request.headers.get("authorization")
    observed.append({
        "method": request.method,
        "path": request.url.path,
        "has_expected_dummy_key": authorization in {
            "Bearer browser-discovery-dummy-key",
            "Bearer empty-dummy-key", "Bearer denied-dummy-key",
            "Bearer alternate-dummy-key",
        },
    })
    if authorization == "Bearer denied-dummy-key":
        return JSONResponse(status_code=401, content={"error": {"message": "denied-dummy-key"}})
    models = [] if authorization == "Bearer empty-dummy-key" else [
        "model-beta", "model-alpha", "model-alpha",
    ]
    if authorization == "Bearer alternate-dummy-key":
        models = ["alternate-key-only-model"]
    return {
        "object": "list",
        "data": [
            {"id": model, "object": "model", "created": 0, "owned_by": "fixture"}
            for model in models
        ],
    }


@app.get("/fixture/observed")
def fixture_observed():
    return observed
