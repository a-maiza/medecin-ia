from typing import TYPE_CHECKING, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import ARRAY, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin

if TYPE_CHECKING:
    from .consultation import Consultation
    from .medecin import Medecin


class ValidationMetric(UUIDMixin, Base):
    """Per-consultation quality metrics recorded when a doctor validates a SOAP note."""

    __tablename__ = "validation_metric"

    consultation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("consultation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    medecin_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("medecin.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    correction_types: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(sa.Text), nullable=True
    )
    time_to_validate_seconds: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )

    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )

    # ── Relations ──────────────────────────────────────────────────────────────
    consultation: Mapped["Consultation"] = relationship(
        "Consultation", back_populates="validation_metrics"
    )
    medecin: Mapped["Medecin"] = relationship(
        "Medecin", back_populates="validation_metrics"
    )


class TrainingPair(UUIDMixin, Base):
    """(raw_soap, corrected_soap) pairs used for fine-tuning or RLHF."""

    __tablename__ = "training_pair"

    consultation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("consultation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    medecin_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("medecin.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    raw_soap: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    corrected_soap: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    diff_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Tags for filtering during training (e.g. specialite, type of correction)
    tags: Mapped[Optional[list[str]]] = mapped_column(ARRAY(sa.Text), nullable=True)

    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )
