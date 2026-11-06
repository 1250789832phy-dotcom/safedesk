from fastapi import HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agent.settings import ModelConfiguration, ModelCredentials


def register_settings(app, settings):
    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exception):
        if request.url.path.startswith("/settings/"):
            return JSONResponse(
                status_code=422,
                content={"detail": "配置格式无效，请检查 URL、模型名称和接口协议。"},
            )
        return await request_validation_exception_handler(request, exception)

    @app.middleware("http")
    async def local_settings_only(request: Request, call_next):
        if request.url.path.startswith("/settings/"):
            host = request.url.hostname
            origin = request.headers.get("origin")
            expected_origin = f"{request.url.scheme}://{request.url.netloc}"
            allowed = (
                host in {"127.0.0.1", "localhost", "::1"}
                and request.client is not None
                and request.client.host in {"127.0.0.1", "::1", "testclient"}
                and (not origin or origin == expected_origin)
            )
            if request.method != "GET":
                allowed = allowed and request.headers.get("X-SafeDesk-Settings") == "1"
            if not allowed:
                return JSONResponse(status_code=403, content={"detail": "仅允许本机页面访问模型设置。"})
        response = await call_next(request)
        if request.url.path.startswith("/settings/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def persist(operation):
        try:
            return operation()
        except ValueError:
            raise HTTPException(400, "首次配置或修改 URL 时，请重新填写 API Key。") from None
        except Exception:
            raise HTTPException(
                500, "无法加密或保存配置；原配置保持不变。请检查本地文件权限及 Windows 用户。"
            ) from None

    @app.get("/settings/model")
    def model_status():
        return settings.status()

    @app.put("/settings/model")
    def save_model(configuration: ModelConfiguration):
        return persist(lambda: settings.save(configuration))

    @app.post("/settings/model/mock")
    def use_mock():
        return persist(settings.use_mock)

    @app.delete("/settings/model")
    def forget_model():
        return persist(lambda: settings.use_mock(forget=True))

    @app.post("/settings/model/test")
    def test_model(configuration: ModelConfiguration):
        try:
            settings.probe(configuration)
        except ValueError:
            raise HTTPException(400, "首次配置或修改 URL 时，请重新填写 API Key。") from None
        except Exception:
            raise HTTPException(
                502, "连接测试失败，请核对 URL、密钥、模型名称及协议，或检查网络和服务额度。"
            ) from None
        return {"ok": True, "message": "模型接口已响应；尚未验证任务规划及工具执行效果。"}

    @app.post("/settings/model/models")
    def discover_models(credentials: ModelCredentials):
        try:
            result = settings.discover_models(credentials)
        except ValueError:
            raise HTTPException(400, "首次配置或修改 URL 时，请重新填写 API Key。") from None
        except Exception as error:
            status = getattr(error, "status_code", None)
            reasons = {
                401: "密钥无效或已过期",
                403: "密钥没有模型列表访问权限",
                404: "上游不支持模型列表接口，或 Base URL 不正确",
                429: "请求过于频繁或服务额度受限",
            }
            reason = reasons.get(status, "请检查地址、网络及上游接口兼容性")
            raise HTTPException(
                502, f"获取模型失败：{reason}。仍可手动填写模型名称。"
            ) from None
        count = len(result["models"])
        if count:
            suffix = "（列表已截断）" if result["truncated"] else ""
            message = f"上游返回 {count} 个模型{suffix}，调用权限及协议尚未验证。"
        else:
            message = "上游未返回可选择的模型，仍可手动填写模型名称。"
        return {**result, "message": message}
