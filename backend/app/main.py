from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router as api_v1_router
from app.database import init_db
from app.services.analyzer import AnalyzerError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="JobRecall Local Service",
        description="Local-first job application memory, duplicate detector, and evidence-backed eligibility analyzer.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Allow Chrome extension and localhost origins; X-JobRecall-Token enforces request authentication
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(chrome-extension://.*|http://127\.0\.0\.1(:\d+)?|http://localhost(:\d+)?)$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AnalyzerError)
    async def handle_analyzer_error(request: Request, exc: AnalyzerError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.message}},
        )

    app.include_router(api_v1_router)
    return app


app = create_app()
