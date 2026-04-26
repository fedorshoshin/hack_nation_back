from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field


class AgentCampaignVariantCreate(BaseModel):
    link: str = Field(..., examples=["https://example.com/checkout-a"])
    name: str = Field(..., examples=["Variant A"])


class AgentCampaignCreate(BaseModel):
    variants: list[AgentCampaignVariantCreate] = Field(..., min_length=1)
    budget: int = Field(..., gt=0)
    number_of_tests: int = Field(..., gt=0)
    success_event: str = Field(..., examples=["task_completed"])
    task: str = Field(..., examples=["Try to complete checkout and report if anything feels confusing."])


class AgentCampaignVariantResponse(BaseModel):
    id: str
    link: str | None
    name: str | None

    model_config = ConfigDict(from_attributes=True)


class AgentCampaignResponse(BaseModel):
    id: str
    variants: list[AgentCampaignVariantResponse]
    budget: int
    number_of_tests: int
    success_event: str
    task: str
    payment_invoice: str | None = None
    payment_hash: str | None = None
    payment_status: str

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def campaign_id(self) -> str:
        return self.id


class UserVariantResponse(BaseModel):
    id: str | None = None
    link: str | None
    name: str | None


class UserCurrentVariantResponse(BaseModel):
    campaign_id: str
    variant: UserVariantResponse
    success_event: str
    task: str


class UserCompletedTaskCreate(BaseModel):
    campaign_id: str
    user_id: str
    variant_id: str | None = None
    variant_name: str | None = None
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        examples=[
            {
                "duration_ms": 42000,
                "clicks": 12,
                "completed": True,
                "friction_score": 2,
            }
        ],
    )
    success_event: str = Field(..., examples=["checkout_completed"])


class UserCompletedTaskResponse(BaseModel):
    id: str
    campaign_id: str
    user_id: str
    variant_id: str | None = None
    variant_name: str | None = None
    metrics: dict[str, Any]
    success_event: str
    payout_sats: int | None = None
    payout_status: str
    payout_preimage: str | None = None
    payout_ln_address: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AgentPaymentStatusCreate(BaseModel):
    campaign_id: str
    payment_hash: str


class AgentPaymentStatusResponse(BaseModel):
    campaign_id: str
    payment_hash: str
    payment_status: str


class AgentCompletedCampaignVariantMetrics(BaseModel):
    variant_id: str | None
    variant_name: str | None
    variant_link: str | None
    completed_tests: int
    numeric_averages: dict[str, float]
    numeric_totals: dict[str, float]
    metrics: list[dict[str, Any]]


class AgentCompletedCampaignResponse(BaseModel):
    campaign_id: str
    successed: bool
    completed_tests: int
    required_tests: int
    success_event: str
    task: str
    metrics: dict[str, Any]
    variants: list[AgentCompletedCampaignVariantMetrics]


class TesterAssignmentRequest(BaseModel):
    external_session_id: str = Field(..., examples=["browser-session-123"])
    user_agent: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaymentCreate(BaseModel):
    campaign_id: str
    amount_sats: int = Field(..., gt=0)
    tests_purchased: int = Field(..., gt=0)
    commission_rate: float = Field(default=0.1, ge=0, lt=1)


class ClientCallRequest(BaseModel):
    client_id: str = Field(..., examples=["client-123"])
    message: str = Field(..., examples=["Hello from the client"])
    metadata: dict[str, Any] = Field(default_factory=dict)


class InternalCallRequest(BaseModel):
    source: str = Field(..., examples=["scheduler"])
    action: str = Field(..., examples=["refresh"])
    payload: dict[str, Any] = Field(default_factory=dict)


class InitSessionRequest(BaseModel):
    campaign_id: str = Field(alias="campaignId")
    session_id: str = Field(alias="sessionId")
    variant: str
    debug: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class InitSessionResponse(BaseModel):
    session_id: str
    campaign_id: str
    variant: str
    internal_session_id: str


class TrackEventRequest(BaseModel):
    campaign_id: str = Field(alias="campaignId")
    session_id: str = Field(alias="sessionId")
    variant: str
    event_type: str = Field(alias="eventType")
    payload: dict[str, Any] = Field(default_factory=dict)


class CompleteTaskRequest(BaseModel):
    campaign_id: str = Field(alias="campaignId")
    session_id: str = Field(alias="sessionId")
    variant: str
    task_id: str = Field(alias="taskId")
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class CompleteTaskResponse(BaseModel):
    ok: bool
    task_id: str
    session_id: str


class VariantStats(BaseModel):
    variant: str
    sessions: int
    completions: int
    conversion_rate: float


class CampaignStatsResponse(BaseModel):
    campaign_id: str
    variants: list[VariantStats]
