import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from nostr_sdk import PayInvoiceRequest

from payment_controller import PaymentController, NWC_AGENT, NWC_PLATFORM


@dataclass
class AccessChallenge:
    macaroon: str
    amount_sats: int
    invoice: str
    payment_hash: str | None
    status: str
    created_at: str
    updated_at: str


class AgentRequest(BaseModel):
    amount_sats: int = Field(
        gt=0,
        description="Amount sender wants to pay in satoshis",
    )
    body: dict[str, Any] = Field(
        default_factory=dict,
        description="Abstract API request body (shape can evolve later)",
    )


class InvoiceChallengeResponse(BaseModel):
    status: str
    message: str
    macaroon: str
    invoice: str
    payment_hash: str | None
    amount_sats: int
    token_example: str


class ProtectedDataResponse(BaseModel):
    status: str
    data: dict[str, Any]


class ChallengeStatusResponse(BaseModel):
    macaroon: str
    status: str
    payment_hash: str | None
    updated_at: str


class SimulatePayRequest(BaseModel):
    macaroon: str = Field(min_length=1)


class AccessChallengeResponse(BaseModel):
    macaroon: str
    amount_sats: int
    invoice: str
    payment_hash: str | None
    preimage: str | None = None
    status: str
    created_at: str
    updated_at: str


app = FastAPI(title="AI Agent Payments API", version="1.0.0")
controller = PaymentController(NWC_AGENT, NWC_PLATFORM)
challenges: dict[str, AccessChallenge] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(bytes.fromhex(value)).hexdigest()


def _to_challenge_response(record: AccessChallenge) -> AccessChallengeResponse:
    return AccessChallengeResponse(
        macaroon=record.macaroon,
        amount_sats=record.amount_sats,
        invoice=record.invoice,
        payment_hash=record.payment_hash,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _parse_l402(authorization: str | None) -> tuple[str, str] | None:
    if not authorization:
        return None

    token: str | None = None
    if authorization.startswith("L402 "):
        token = authorization.removeprefix("L402 ").strip()
    elif authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()

    if not token or ":" not in token:
        return None

    macaroon, preimage = token.split(":", 1)
    if not macaroon or not preimage:
        return None
    return macaroon, preimage


async def _refresh_challenge_status(record: AccessChallenge) -> None:
    try:
        status = await controller.lookup_invoice_status(
            "platform",
            payment_hash=record.payment_hash,
            invoice=record.invoice,
        )
        record.status = status.state.name.lower()
        record.updated_at = _now_iso()
    except Exception:
        # Keep last known state if remote lookup fails transiently.
        pass


async def _issue_invoice_challenge(amount_sats: int) -> AccessChallenge:
    created_at = _now_iso()
    macaroon = secrets.token_hex(16)
    invoice = await controller.create_invoice(
        "platform",
        amount_sats,
        "API access payment challenge",
    )
    record = AccessChallenge(
        macaroon=macaroon,
        amount_sats=amount_sats,
        invoice=invoice.invoice,
        payment_hash=invoice.payment_hash,
        status="pending",
        created_at=created_at,
        updated_at=created_at,
    )
    challenges[macaroon] = record
    return record


async def _is_token_authorized(macaroon: str, preimage: str) -> bool:
    record = challenges.get(macaroon)
    if not record:
        return False

    await _refresh_challenge_status(record)
    if record.status != "settled":
        return False

    try:
        if record.payment_hash and _sha256_hex(preimage) != record.payment_hash:
            return False
    except ValueError:
        return False

    status = await controller.lookup_invoice_status(
        "platform",
        payment_hash=record.payment_hash,
        invoice=record.invoice,
    )
    if not status.preimage:
        return True
    return status.preimage == preimage


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/protected-resource", response_model=ProtectedDataResponse)
async def protected_resource(
    payload: AgentRequest,
    authorization: str | None = Header(default=None),
) -> ProtectedDataResponse:
    parsed = _parse_l402(authorization)
    if parsed:
        macaroon, preimage = parsed
        if await _is_token_authorized(macaroon, preimage):
            return ProtectedDataResponse(
                status="authorized",
                data={
                    "result": "Access granted to protected data",
                    "echo": payload.body,
                    "notes": "This is abstract placeholder data for now.",
                },
            )

    challenge = await _issue_invoice_challenge(payload.amount_sats)
    token_example = f"L402 {challenge.macaroon}:<preimage-from-paid-invoice>"
    www_authenticate = (
        f'L402 macaroon="{challenge.macaroon}", '
        f'invoice="{challenge.invoice}", '
        f'payment_hash="{challenge.payment_hash}", '
        f'amount="{challenge.amount_sats}"'
    )

    raise HTTPException(
        status_code=402,
        detail=InvoiceChallengeResponse(
            status="payment_required",
            message="Provide valid macaroon + preimage in Authorization header",
            macaroon=challenge.macaroon,
            invoice=challenge.invoice,
            payment_hash=challenge.payment_hash,
            amount_sats=challenge.amount_sats,
            token_example=token_example,
        ).model_dump(),
        headers={
            "WWW-Authenticate": www_authenticate
        },
    )


@app.get("/v1/challenges/{macaroon}", response_model=ChallengeStatusResponse)
async def get_challenge_status(macaroon: str) -> ChallengeStatusResponse:
    record = challenges.get(macaroon)
    if not record:
        raise HTTPException(status_code=404, detail="Macaroon not found")
    await _refresh_challenge_status(record)
    return ChallengeStatusResponse(
        macaroon=record.macaroon,
        status=record.status,
        payment_hash=record.payment_hash,
        updated_at=record.updated_at,
    )


@app.post("/v1/challenges/simulate-pay", response_model=AccessChallengeResponse)
async def simulate_challenge_payment(payload: SimulatePayRequest) -> AccessChallengeResponse:
    record = challenges.get(payload.macaroon)
    if not record:
        raise HTTPException(status_code=404, detail="Macaroon not found")
    pay_res = await controller.agent.pay_invoice(
        # Convenience endpoint for local testing only.
        PayInvoiceRequest(id=None, invoice=record.invoice, amount=None)
    )
    await _refresh_challenge_status(record)
    response = _to_challenge_response(record)
    response.preimage = pay_res.preimage
    return response