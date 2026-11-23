from fastapi import APIRouter, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse

from evaluation.hub import MAX_VERSION, Dataset, EvalHub, RunConfig


def register_evaluation(app: FastAPI, hub: EvalHub) -> None:
    @app.middleware("http")
    async def local_evaluation_only(request: Request, call_next):
        if not request.url.path.startswith("/eval/"):
            return await call_next(request)
        origin = request.headers.get("origin")
        same_origin = f"{request.url.scheme}://{request.url.netloc}"
        if (
            request.url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or request.client is None
            or request.client.host not in {"127.0.0.1", "::1", "testclient"}
            or (origin is not None and origin != same_origin)
        ):
            return JSONResponse(
                status_code=403, content={"detail": "仅允许本机页面访问评测数据。"},
                headers={"Cache-Control": "no-store"},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(evaluation_router(hub))


def evaluation_router(hub: EvalHub) -> APIRouter:
    router = APIRouter(prefix="/eval", tags=["evaluation"])

    @router.post("/datasets", status_code=201)
    def register_dataset(dataset: Dataset):
        return hub.register(dataset)

    @router.get("/datasets")
    def list_datasets(limit: int = Query(100, ge=1, le=100)):
        return hub.list_datasets(limit=limit)

    @router.get("/datasets/{name}/{version}")
    def get_dataset(name: str, version: int = Path(ge=1, le=MAX_VERSION)):
        try:
            return hub.get_dataset(name, version)
        except KeyError as error:
            raise HTTPException(404, error.args[0]) from error

    @router.post("/runs", status_code=201)
    def run_evaluation(config: RunConfig):
        try:
            return hub.run(**config.model_dump())
        except KeyError as error:
            raise HTTPException(404, error.args[0]) from error

    @router.get("/runs")
    def list_runs(limit: int = Query(50, ge=1, le=100)):
        return hub.list_runs(limit=limit)

    @router.get("/runs/{run_id}")
    def get_run(run_id: str):
        try:
            return hub.get_run(run_id)
        except KeyError as error:
            raise HTTPException(404, error.args[0]) from error

    @router.get("/compare")
    def compare_runs(baseline: str, candidate: str):
        try:
            return hub.compare(baseline, candidate)
        except KeyError as error:
            raise HTTPException(404, error.args[0]) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    return router
