from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class CampaignStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class EventType(str, Enum):
    SDK_INIT = "sdk_init"
    PAGE_VIEW = "page_view"
    BUTTON_CLICK = "button_click"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    ERROR = "error"


class TaskStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    SETTLED = "settled"
    FAILED = "failed"


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    number_of_tests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_event: Mapped[str] = mapped_column(String(128), nullable=False, default="task_completed")
    task: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payment_invoice: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")

    status: Mapped[CampaignStatus] = mapped_column(
        SqlEnum(CampaignStatus),
        default=CampaignStatus.DRAFT,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    variants: Mapped[list["Variant"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )
    user_assignments: Mapped[list["UserCampaignAssignment"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )
    user_completed_tasks: Mapped[list["UserCompletedTask"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )


class Variant(Base):
    __tablename__ = "variants"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)

    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    campaign: Mapped["Campaign"] = relationship(back_populates="variants")
    sessions: Mapped[list["Session"]] = relationship(back_populates="variant")
    events: Mapped[list["Event"]] = relationship(back_populates="variant")
    user_assignments: Mapped[list["UserCampaignAssignment"]] = relationship(
        back_populates="variant",
    )

    __table_args__ = (
        UniqueConstraint("campaign_id", "key", name="uq_variant_campaign_key"),
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )

    variant_id: Mapped[str] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE"),
        nullable=False,
    )

    external_session_id: Mapped[str] = mapped_column(String(255), nullable=False)

    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    campaign: Mapped["Campaign"] = relationship(back_populates="sessions")
    variant: Mapped["Variant"] = relationship(back_populates="sessions")

    events: Mapped[list["Event"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )

    task_completions: Mapped[list["TaskCompletion"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "external_session_id",
            name="uq_session_campaign_external_session_id",
        ),
        Index("ix_sessions_campaign_variant", "campaign_id", "variant_id"),
    )


class UserCampaignAssignment(Base):
    __tablename__ = "user_campaign_assignments"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_id: Mapped[str] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    campaign: Mapped["Campaign"] = relationship(back_populates="user_assignments")
    variant: Mapped["Variant"] = relationship(back_populates="user_assignments")

    __table_args__ = (
        UniqueConstraint("user_id", "campaign_id", name="uq_user_campaign_assignment"),
        Index("ix_user_campaign_assignments_user", "user_id"),
    )


class UserCompletedTask(Base):
    __tablename__ = "user_completed_tasks"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    variant_id: Mapped[str | None] = mapped_column(
        ForeignKey("variants.id", ondelete="SET NULL"),
        nullable=True,
    )
    variant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    success_event: Mapped[str] = mapped_column(String(128), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    payout_sats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payout_status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    payout_preimage: Mapped[str | None] = mapped_column(Text, nullable=True)
    payout_ln_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    campaign: Mapped["Campaign"] = relationship(back_populates="user_completed_tasks")
    variant: Mapped["Variant | None"] = relationship()

    __table_args__ = (
        Index("ix_user_completed_tasks_campaign_user", "campaign_id", "user_id"),
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_id: Mapped[str] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[EventType] = mapped_column(SqlEnum(EventType), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    session: Mapped["Session"] = relationship(back_populates="events")
    variant: Mapped["Variant"] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_events_campaign_variant_type", "campaign_id", "variant_id", "event_type"),
        Index("ix_events_session_created_at", "session_id", "created_at"),
    )


class TaskCompletion(Base):
    __tablename__ = "task_completions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(SqlEnum(TaskStatus), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    session: Mapped["Session"] = relationship(back_populates="task_completions")

    __table_args__ = (
        UniqueConstraint("session_id", "task_key", name="uq_task_completion_session_task"),
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount_sats: Mapped[int] = mapped_column(Integer, nullable=False)
    commission_sats: Mapped[int] = mapped_column(Integer, nullable=False)
    tester_pool_sats: Mapped[int] = mapped_column(Integer, nullable=False)
    tests_purchased: Mapped[int] = mapped_column(Integer, nullable=False)
    lightning_invoice: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PaymentStatus] = mapped_column(
        SqlEnum(PaymentStatus),
        default=PaymentStatus.PENDING,
        nullable=False,
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    campaign: Mapped["Campaign"] = relationship(back_populates="payments")

    @property
    def payout_per_test_sats(self) -> int:
        if self.tests_purchased <= 0:
            return 0
        return self.tester_pool_sats // self.tests_purchased
