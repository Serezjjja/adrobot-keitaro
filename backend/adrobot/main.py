from collections.abc import Generator
from contextlib import asynccontextmanager
from threading import Lock
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from adrobot import campaigns, editor
from adrobot.config import Settings
from adrobot.errors import AppError
from adrobot.keitaro import Keitaro
from adrobot.schema import (
    MAX_SAFE_ID,
    Catalogs,
    CreateCampaign,
    DraftChange,
    DraftVersion,
    Resource,
    StreamView,
)

router = APIRouter(prefix="/api")
ResourceId = Annotated[int, Path(gt=0, le=MAX_SAFE_ID)]


def get_session(request: Request) -> Generator[Session, None, None]:
    with request.app.state.sessions() as session:
        yield session


def get_tracker(request: Request) -> Keitaro:
    return request.app.state.tracker


Db = Annotated[Session, Depends(get_session)]
Tracker = Annotated[Keitaro, Depends(get_tracker)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/settings")
def settings(request: Request) -> dict[str, str]:
    config = request.app.state.settings
    base = urlsplit(str(config.keitaro_base_url))
    admin_url = urlunsplit(
        (base.scheme, base.netloc.rsplit("@", 1)[-1], f"{base.path.rstrip('/')}/admin/", "", "")
    )
    return {"campaign_prefix": config.campaign_prefix, "keitaro_admin_url": admin_url}


@router.get("/catalogs")
def catalogs(tracker: Tracker) -> Catalogs:
    return tracker.catalogs()


@router.get("/campaigns")
def list_campaigns(tracker: Tracker) -> list[Resource]:
    return [
        Resource(id=campaign.id, name=campaign.name)
        for campaign in tracker.campaigns()
        if campaign.state != "deleted"
    ]


@router.post("/campaigns", status_code=201)
def create_campaign(request: Request, db: Db, tracker: Tracker, data: CreateCampaign) -> Resource:
    with request.app.state.mutations:
        return campaigns.create_campaign(
            db, tracker, data, request.app.state.settings.campaign_prefix
        )


@router.get("/offers")
def offers(
    tracker: Tracker,
    db: Db,
    q: Annotated[str, Query(max_length=100)] = "",
    stream_id: Annotated[int | None, Query(gt=0, le=MAX_SAFE_ID)] = None,
) -> list[Resource]:
    exclude_ids = editor.attached_offer_ids(db, stream_id) if stream_id is not None else set()
    return campaigns.search_offers(tracker, q, exclude_ids=exclude_ids)


@router.get("/campaigns/{campaign_id}/streams")
def list_streams(campaign_id: ResourceId, db: Db) -> list[StreamView]:
    return editor.list_streams(db, campaign_id)


@router.post("/campaigns/{campaign_id}/sync")
def synchronize(
    request: Request, campaign_id: ResourceId, db: Db, tracker: Tracker
) -> list[StreamView]:
    with request.app.state.mutations:
        return editor.synchronize(db, tracker, campaign_id)


@router.patch("/streams/{stream_id}/draft")
def edit_draft(
    request: Request, stream_id: ResourceId, db: Db, tracker: Tracker, change: DraftChange
) -> StreamView:
    with request.app.state.mutations:
        return editor.change_draft(db, tracker, stream_id, change)


@router.delete("/streams/{stream_id}/draft")
def cancel_draft(
    request: Request,
    stream_id: ResourceId,
    db: Db,
    version: Annotated[int, Query(ge=1, le=MAX_SAFE_ID)],
) -> StreamView:
    with request.app.state.mutations:
        return editor.cancel_draft(db, stream_id, version)


@router.post("/streams/{stream_id}/publish")
def publish_draft(
    request: Request, stream_id: ResourceId, db: Db, tracker: Tracker, draft: DraftVersion
) -> StreamView:
    with request.app.state.mutations:
        return editor.publish_draft(db, tracker, stream_id, draft.version)


def create_app(
    config: Settings | None = None, transport: httpx.BaseTransport | None = None
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        config_values = config or Settings()
        engine = create_engine(config_values.database_url)
        application.state.settings = config_values
        application.state.sessions = sessionmaker(engine, expire_on_commit=False)
        # One API worker owns draft mutations, including the read/PUT/read sequence in Keitaro.
        application.state.mutations = Lock()
        base = urlsplit(str(config_values.keitaro_base_url))
        api_url = urlunsplit(
            (base.scheme, base.netloc, f"{base.path.rstrip('/')}/admin_api/v1/", "", "")
        )
        try:
            with httpx.Client(
                base_url=api_url,
                headers={"Api-Key": config_values.keitaro_api_key.get_secret_value()},
                timeout=8.0,
                transport=transport,
            ) as client:
                application.state.tracker = Keitaro(client)
                yield
        finally:
            engine.dispose()

    application = FastAPI(title="Adrobot · Keitaro", lifespan=lifespan)

    @application.exception_handler(AppError)
    async def error_response(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    application.include_router(router)
    return application


app = create_app()
