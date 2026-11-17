import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings
from app.errors import AppError
from app.keitaro import Keitaro
from app.schemas import CreateCampaign, EditOffer, Revision
from app.service import CampaignService, present
from app.store import Store

STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, keitaro=None) -> FastAPI:
    settings = settings or Settings()
    api = keitaro or Keitaro(settings.keitaro_base_url, settings.keitaro_api_key)
    service = CampaignService(Store(settings.database_path), api)

    @asynccontextmanager
    async def lifespan(app):
        yield
        api.close()

    application = FastAPI(title="Adrobot API", version="1.0.0", lifespan=lifespan)
    application.state.service = service
    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
    )

    @application.middleware("http")
    async def security(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            if settings.app_access_token:
                token = request.headers.get("Authorization", "").removeprefix("Bearer ")
                if not hmac.compare_digest(token.encode(), settings.app_access_token.encode()):
                    return JSONResponse({"detail": "Укажите токен доступа к Adrobot."}, 401)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                if origin and origin != str(request.base_url).rstrip("/"):
                    return JSONResponse({"detail": "Запрос с другого сайта запрещён."}, 403)
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    return JSONResponse({"detail": "Ожидается application/json."}, 415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        if request.url.path in {"/docs", "/redoc"}:
            # FastAPI's documentation uses a CDN and an inline bootstrap script.
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
            )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(AppError)
    async def handle_error(request, exc):
        return JSONResponse({"detail": exc.message}, exc.status)

    @application.exception_handler(RequestValidationError)
    async def handle_validation(request, exc):
        # Do not reflect arbitrary input or secrets into error responses.
        fields = ", ".join(dict.fromkeys(str(e["loc"][-1]) for e in exc.errors()))
        return JSONResponse({"detail": f"Проверьте поля: {fields}."}, 422)

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.get("/api/config")
    def config():
        return {"configured": api.configured}

    @application.get("/api/metadata")
    def metadata():
        return service.metadata()

    @application.get("/api/offers")
    def offers(
        q: str = Query("", max_length=200),
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
    ):
        return service.offers(q, offset, limit)

    @application.get("/api/campaigns")
    def campaigns(
        q: str = Query("", max_length=200),
        offset: int = Query(0, ge=0),
        limit: int = Query(25, ge=1, le=100),
    ):
        return service.campaigns(q, offset, limit)

    @application.post("/api/campaigns", status_code=201)
    def create_campaign(body: CreateCampaign):
        return service.create(body)

    @application.get("/api/campaigns/{campaign_id}")
    def get_campaign(campaign_id: int):
        return present(service.store.get(campaign_id))

    @application.post("/api/campaigns/{campaign_id}/sync")
    def sync(campaign_id: int, body: Revision | None = None):
        return service.sync(campaign_id, body.revision if body else None)

    @application.post("/api/campaigns/{campaign_id}/streams/{stream_id}/offers")
    def edit_offer(campaign_id: int, stream_id: int, body: EditOffer):
        return service.edit(campaign_id, stream_id, body)

    @application.post("/api/campaigns/{campaign_id}/streams/{stream_id}/cancel")
    def cancel(campaign_id: int, stream_id: int, body: Revision):
        return service.cancel(campaign_id, stream_id, body.revision)

    @application.post("/api/campaigns/{campaign_id}/streams/{stream_id}/publish")
    def publish(campaign_id: int, stream_id: int, body: Revision):
        return service.publish(campaign_id, stream_id, body.revision)

    @application.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    application.mount("/static", StaticFiles(directory=STATIC), name="static")
    return application
