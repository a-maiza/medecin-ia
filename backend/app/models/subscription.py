from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin

if TYPE_CHECKING:
    from .cabinet import Cabinet

SubscriptionPlanEnum = Enum(
    "trial", "solo", "cabinet", "reseau",
    name="subscription_plan_enum",
)

SubscriptionStatusEnum = Enum(
    "active", "past_due", "canceled", "unpaid",
    name="subscription_status_enum",
)


class Subscription(UUIDMixin, Base):
    __tablename__ = "subscription"

    cabinet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cabinet.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,   # one subscription per cabinet
        index=True,
    )

    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, unique=True
    )
    plan: Mapped[str] = mapped_column(SubscriptionPlanEnum, nullable=False)
    status: Mapped[str] = mapped_column(
        SubscriptionStatusEnum, nullable=False, server_default="active"
    )

    current_period_start: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )

    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    # ── Relations ──────────────────────────────────────────────────────────────
    cabinet: Mapped["Cabinet"] = relationship("Cabinet", back_populates="subscription")
