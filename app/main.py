from contextlib import asynccontextmanager
from pathlib import Path
from random import choice
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import engine, get_db
from app.models import Base, Campaign, UserCampaignAssignment, UserCompletedTask, Variant
from app.schemas import (
    AgentCampaignCreate,
    AgentCampaignResponse,
    ClientCallRequest,
    CompleteTaskRequest,
    CompleteTaskResponse,
    InternalCallRequest,
    InitSessionRequest,
    InitSessionResponse,
    PaymentCreate,
    TesterAssignmentRequest,
    TrackEventRequest,
    UserCompletedTaskCreate,
    UserCompletedTaskResponse,
    UserCurrentVariantResponse,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Hack Nation Backend",
    description="Backend API for client and internal hackathon service calls.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SDK_PATH = Path(__file__).with_name("peach-sdk.js")


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "hack_nation_back",
        "status": "ok",
        "docs": "/docs",
    }


@app.api_route("/peach-sdk.js", methods=["GET", "HEAD"], include_in_schema=False)
async def peach_sdk() -> FileResponse:
    if not SDK_PATH.exists():
        raise HTTPException(status_code=404, detail="SDK file not found")
    return FileResponse(SDK_PATH, media_type="application/javascript")


@app.post("/client/call")
async def handle_client_call(request: ClientCallRequest) -> dict[str, Any]:
    return {
        "status": "received",
        "type": "client",
        "client_id": request.client_id,
        "message": request.message,
        "metadata": request.metadata,
    }


@app.post("/internal/call")
async def handle_internal_call(request: InternalCallRequest) -> dict[str, Any]:
    return {
        "status": "received",
        "type": "internal",
        "source": request.source,
        "action": request.action,
        "payload": request.payload,
    }


@app.post(
    "/api/agent/campaigns",
    response_model=AgentCampaignResponse,
    tags=["Agent API"],
)
async def create_agent_campaign(
    request: AgentCampaignCreate,
    db: Session = Depends(get_db),
) -> Campaign:
    campaign_id = str(uuid4())
    campaign = Campaign(
        id=campaign_id,
        name=f"Campaign {campaign_id[:8]}",
        budget=request.budget,
        number_of_tests=request.number_of_tests,
        success_event=request.success_event,
        task=request.task,
    )
    for index, variant_request in enumerate(request.variants, start=1):
        campaign.variants.append(
            Variant(
                key=f"variant_{index}",
                name=variant_request.name,
                link=variant_request.link,
                config={"link": variant_request.link},
            )
        )

    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


@app.get(
    "/api/agent/campaigns",
    response_model=AgentCampaignResponse,
    tags=["Agent API"],
)
async def get_agent_campaign(db: Session = Depends(get_db)) -> Campaign:
    campaign = db.scalars(
        select(Campaign)
        .options(selectinload(Campaign.variants))
        .order_by(Campaign.created_at.asc())
        .limit(1)
    ).first()
    if campaign is None:
        raise HTTPException(status_code=404, detail="No campaigns available")
    return campaign


@app.get(
    "/api/user/current_variant",
    response_model=UserCurrentVariantResponse,
    tags=["User API"],
)
async def get_user_current_variant(db: Session = Depends(get_db)) -> UserCurrentVariantResponse:
    user_id = "1"
    used_campaign_ids = select(UserCampaignAssignment.campaign_id).where(
        UserCampaignAssignment.user_id == user_id,
    )
    campaign = db.scalars(
        select(Campaign)
        .options(selectinload(Campaign.variants))
        .where(Campaign.id.not_in(used_campaign_ids))
        .order_by(Campaign.created_at.asc())
        .limit(1)
    ).first()
    if campaign is None:
        raise HTTPException(status_code=404, detail="No unused campaigns available")
    if not campaign.variants:
        raise HTTPException(status_code=404, detail="Campaign has no variants")

    variant = choice(campaign.variants)
    db.add(
        UserCampaignAssignment(
            user_id=user_id,
            campaign_id=campaign.id,
            variant_id=variant.id,
        )
    )
    db.commit()

    return UserCurrentVariantResponse(
        campaign_id=campaign.id,
        variant={
            "link": variant.link,
            "name": variant.name,
        },
        success_event=campaign.success_event,
        task=campaign.task,
    )


@app.post(
    "/api/user/completed_task",
    response_model=UserCompletedTaskResponse,
    tags=["User API"],
)
async def complete_user_task(
    request: UserCompletedTaskCreate,
    db: Session = Depends(get_db),
) -> UserCompletedTask:
    campaign = db.get(Campaign, request.campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    completed_task = UserCompletedTask(
        campaign_id=request.campaign_id,
        user_id=request.user_id,
        metrics=request.metrics,
        success_event=request.success_event,
    )
    db.add(completed_task)
    db.commit()
    db.refresh(completed_task)
    return completed_task


@app.post("/tester/campaigns/{campaign_id}/assignment", tags=["Tester API"])
async def assign_tester_variant(
    campaign_id: str,
    request: TesterAssignmentRequest,
) -> dict[str, Any]:
    return {
        "status": "assigned",
        "campaign_id": campaign_id,
        "external_session_id": request.external_session_id,
        "variant": {
            "key": "a",
            "config": {},
        },
    }


@app.post("/sdk/init", response_model=InitSessionResponse, tags=["SDK API"])
async def init_sdk_session(request: InitSessionRequest) -> InitSessionResponse:
    return InitSessionResponse(
        session_id=request.session_id,
        campaign_id=request.campaign_id,
        variant=request.variant,
        internal_session_id=f"{request.campaign_id}:{request.session_id}",
    )


@app.post("/sdk/events", tags=["SDK API"])
async def track_sdk_event(request: TrackEventRequest) -> dict[str, Any]:
    return {
        "ok": True,
        "event": request.model_dump(by_alias=True),
    }


@app.post("/sdk/tasks/complete", response_model=CompleteTaskResponse, tags=["SDK API"])
async def complete_sdk_task(request: CompleteTaskRequest) -> CompleteTaskResponse:
    return CompleteTaskResponse(
        ok=True,
        task_id=request.task_id,
        session_id=request.session_id,
    )


@app.post("/payments/invoices", tags=["Payment API"])
async def create_payment_invoice(request: PaymentCreate) -> dict[str, Any]:
    commission_sats = int(request.amount_sats * request.commission_rate)
    tester_pool_sats = request.amount_sats - commission_sats

    return {
        "status": "invoice_pending",
        "campaign_id": request.campaign_id,
        "amount_sats": request.amount_sats,
        "commission_sats": commission_sats,
        "tester_pool_sats": tester_pool_sats,
        "payout_per_test_sats": tester_pool_sats // request.tests_purchased,
        "lightning_invoice": None,
    }
