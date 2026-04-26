from contextlib import asynccontextmanager
from collections import defaultdict
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
from app.models import Base, Campaign, CampaignStatus, UserCampaignAssignment, UserCompletedTask, Variant
from app.payment_controller import payment_controller
from app.schemas import (
    AgentCampaignCreate,
    AgentCampaignResponse,
    AgentCompletedCampaignResponse,
    AgentCompletedCampaignVariantMetrics,
    AgentPaymentStatusCreate,
    AgentPaymentStatusResponse,
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
LLMS_PATH = Path(__file__).with_name("llms.txt")
TESTER_PAYOUT_LN_ADDRESS = "nwc1777195149882@getalby.com"


def _numeric_metric_summary(metrics: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for metric in metrics:
        for key, value in metric.items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            totals[key] += float(value)
            counts[key] += 1

    averages = {
        key: totals[key] / counts[key]
        for key in totals
        if counts[key] > 0
    }
    return {
        "numeric_averages": averages,
        "numeric_totals": dict(totals),
    }


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


@app.api_route("/llms.txt", methods=["GET", "HEAD"], include_in_schema=False)
async def llms_txt() -> FileResponse:
    if not LLMS_PATH.exists():
        raise HTTPException(status_code=404, detail="LLM guide not found")
    return FileResponse(LLMS_PATH, media_type="text/plain")


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
    try:
        invoice = await payment_controller.create_invoice(
            "platform",
            request.budget,
            f"Campaign {campaign_id} test budget",
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to create Lightning invoice: {error}",
        ) from error

    campaign = Campaign(
        id=campaign_id,
        name=f"Campaign {campaign_id[:8]}",
        budget=request.budget,
        number_of_tests=request.number_of_tests,
        success_event=request.success_event,
        task=request.task,
        payment_invoice=invoice.invoice,
        payment_hash=invoice.payment_hash,
        payment_status="pending",
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


@app.post(
    "/api/agent/payment_status",
    response_model=AgentPaymentStatusResponse,
    tags=["Agent API"],
)
async def get_agent_payment_status(
    request: AgentPaymentStatusCreate,
    db: Session = Depends(get_db),
) -> AgentPaymentStatusResponse:
    campaign = db.get(Campaign, request.campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.payment_hash != request.payment_hash:
        raise HTTPException(status_code=400, detail="Payment hash does not match campaign")

    try:
        payment_status = await payment_controller.lookup_invoice_status(
            "platform",
            payment_hash=request.payment_hash,
            invoice=campaign.payment_invoice,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to lookup Lightning invoice: {error}",
        ) from error

    campaign.payment_status = payment_status.state.name.lower()
    if campaign.payment_status == "settled":
        campaign.status = CampaignStatus.ACTIVE
    db.add(campaign)
    db.commit()

    return AgentPaymentStatusResponse(
        campaign_id=campaign.id,
        payment_hash=request.payment_hash,
        payment_status=campaign.payment_status,
    )


@app.get(
    "/api/agent/campaigns",
    response_model=AgentCampaignResponse,
    tags=["Agent API"],
)
async def get_agent_campaign(db: Session = Depends(get_db)) -> Campaign:
    campaign = db.scalars(
        select(Campaign)
        .options(selectinload(Campaign.variants))
        .where(Campaign.payment_status == "settled")
        .order_by(Campaign.created_at.asc())
        .limit(1)
    ).first()
    if campaign is None:
        raise HTTPException(status_code=404, detail="No campaigns available")
    return campaign


@app.get(
    "/api/agent/completed_campaign",
    response_model=list[AgentCompletedCampaignResponse],
    tags=["Agent API"],
)
async def get_agent_completed_campaigns(db: Session = Depends(get_db)) -> list[AgentCompletedCampaignResponse]:
    campaigns = db.scalars(
        select(Campaign)
        .options(selectinload(Campaign.variants))
        .where(Campaign.payment_status == "settled")
        .order_by(Campaign.created_at.asc())
    ).all()
    if not campaigns:
        return []

    campaign_ids = [campaign.id for campaign in campaigns]
    completions = db.scalars(
        select(UserCompletedTask)
        .where(UserCompletedTask.campaign_id.in_(campaign_ids))
        .order_by(UserCompletedTask.completed_at.asc())
    ).all()
    assignments = db.scalars(
        select(UserCampaignAssignment)
        .where(UserCampaignAssignment.campaign_id.in_(campaign_ids))
    ).all()

    completions_by_campaign: dict[str, list[UserCompletedTask]] = defaultdict(list)
    for completion in completions:
        completions_by_campaign[completion.campaign_id].append(completion)

    assignment_by_campaign_user = {
        (assignment.campaign_id, assignment.user_id): assignment
        for assignment in assignments
    }

    responses: list[AgentCompletedCampaignResponse] = []
    for campaign in campaigns:
        campaign_completions = completions_by_campaign[campaign.id]
        completed_tests = len(campaign_completions)
        successed = completed_tests >= campaign.number_of_tests
        if successed and campaign.status == CampaignStatus.ACTIVE:
            campaign.status = CampaignStatus.ARCHIVED
            db.add(campaign)

        variant_by_id = {variant.id: variant for variant in campaign.variants}
        metrics_by_variant: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        variant_name_by_id: dict[str | None, str | None] = {}
        for completion in campaign_completions:
            assignment = assignment_by_campaign_user.get((completion.campaign_id, completion.user_id))
            variant_id = completion.variant_id
            if variant_id is None and assignment is not None:
                variant_id = assignment.variant_id
            if completion.variant_name is not None:
                variant_name_by_id[variant_id] = completion.variant_name
            metrics_by_variant[variant_id].append(completion.metrics)

        variant_metrics: list[AgentCompletedCampaignVariantMetrics] = []
        for variant_id, metrics in metrics_by_variant.items():
            variant = variant_by_id.get(variant_id) if variant_id is not None else None
            summary = _numeric_metric_summary(metrics)
            variant_metrics.append(
                AgentCompletedCampaignVariantMetrics(
                    variant_id=variant_id,
                    variant_name=variant.name if variant is not None else variant_name_by_id.get(variant_id),
                    variant_link=variant.link if variant is not None else None,
                    completed_tests=len(metrics),
                    numeric_averages=summary["numeric_averages"],
                    numeric_totals=summary["numeric_totals"],
                    metrics=metrics,
                )
            )

        campaign_metrics = _numeric_metric_summary([completion.metrics for completion in campaign_completions])
        responses.append(
            AgentCompletedCampaignResponse(
                campaign_id=campaign.id,
                successed=successed,
                completed_tests=completed_tests,
                required_tests=campaign.number_of_tests,
                success_event=campaign.success_event,
                task=campaign.task,
                metrics={
                    **campaign_metrics,
                    "raw": [completion.metrics for completion in campaign_completions],
                },
                variants=variant_metrics,
            )
        )

    db.commit()
    return responses


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
        .where(Campaign.payment_status == "settled")
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
            "id": variant.id,
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
    if campaign.payment_status != "settled":
        raise HTTPException(status_code=402, detail="Campaign payment is not settled")
    if campaign.number_of_tests <= 0:
        raise HTTPException(status_code=400, detail="Campaign number_of_tests must be greater than zero")

    variant_id = request.variant_id
    variant_name = request.variant_name
    if variant_id is not None:
        variant = db.get(Variant, variant_id)
        if variant is None or variant.campaign_id != campaign.id:
            raise HTTPException(status_code=400, detail="Variant does not belong to campaign")
        variant_name = variant_name or variant.name
    elif variant_name is not None:
        variant = db.scalars(
            select(Variant)
            .where(Variant.campaign_id == campaign.id)
            .where(Variant.name == variant_name)
            .limit(1)
        ).first()
        if variant is not None:
            variant_id = variant.id

    existing_completion = db.scalars(
        select(UserCompletedTask)
        .where(UserCompletedTask.campaign_id == request.campaign_id)
        .where(UserCompletedTask.user_id == request.user_id)
        .where(UserCompletedTask.payout_status == "paid")
        .order_by(UserCompletedTask.completed_at.desc())
        .limit(1)
    ).first()
    if existing_completion is not None:
        return existing_completion

    payout_sats = campaign.budget // campaign.number_of_tests
    if payout_sats <= 0:
        raise HTTPException(status_code=400, detail="Campaign payout per test is zero")

    completed_task = UserCompletedTask(
        campaign_id=request.campaign_id,
        user_id=request.user_id,
        variant_id=variant_id,
        variant_name=variant_name,
        metrics=request.metrics,
        success_event=request.success_event,
        payout_sats=payout_sats,
        payout_status="pending",
        payout_ln_address=TESTER_PAYOUT_LN_ADDRESS,
    )
    db.add(completed_task)
    db.commit()
    db.refresh(completed_task)

    try:
        payout_preimage = await payment_controller.payout_to_lnaddress(
            "platform",
            TESTER_PAYOUT_LN_ADDRESS,
            payout_sats,
        )
    except Exception as error:
        completed_task.payout_status = "failed"
        db.add(completed_task)
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=f"Failed to send Lightning payout: {error}",
        ) from error

    completed_task.payout_status = "paid"
    completed_task.payout_preimage = payout_preimage
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
