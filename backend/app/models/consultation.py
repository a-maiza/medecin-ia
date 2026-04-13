from typing import TYPE_CHECKING, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import ARRAY, Enum, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .cabinet import Cabinet
    from .medecin import Medecin
    from .metrics import ValidationMetric
    from .patient import Patient

ConsultationStatusEnum = Enum(
    "in_progress", "generated", "validated", "exported",
    name="consultation_status_enum",
)


class Consultation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "consultation"

    cabinet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cabinet.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    medecin_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("medecin.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    patient_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patient.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    date: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    motif: Mapped[str] = mapped_column(Text, nullable=False)

    # Chiffré AES-256-GCM
    transcript_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    soap_generated: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    soap_validated: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    correction_types: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(sa.Text), nullable=True
    )
    status: Mapped[str] = mapped_column(
        ConsultationStatusEnum,
        nullable=False,
        server_default="in_progress",
    )
    alerts: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    chunks_used: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(sa.Text), nullable=True
    )
    dmp_document_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    doctolib_consultation_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )

    # ── Relations ──────────────────────────────────────────────────────────────
    cabinet: Mapped["Cabinet"] = relationship("Cabinet")
    medecin: Mapped["Medecin"] = relationship("Medecin", back_populates="consultations")
    patient: Mapped["Patient"] = relationship("Patient", back_populates="consultations")
    validation_metrics: Mapped[list["ValidationMetric"]] = relationship(
        "ValidationMetric", back_populates="consultation"
    )
