from typing import TYPE_CHECKING, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Boolean, Enum, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .cabinet import Cabinet
    from .consultation import Consultation

SexeEnum = Enum("M", "F", "autre", name="sexe_enum")


class Patient(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "patient"
    __table_args__ = (
        UniqueConstraint("cabinet_id", "ins", name="uq_patient_cabinet_ins"),
    )

    cabinet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cabinet.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    ins: Mapped[Optional[str]] = mapped_column(String(22), nullable=True)

    # Champs chiffrés AES-256-GCM — stockés en base64, déchiffrés à la volée
    nom_pseudonyme: Mapped[str] = mapped_column(Text, nullable=False)
    date_naissance_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sexe: Mapped[Optional[str]] = mapped_column(SexeEnum, nullable=True)
    allergies_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    traitements_actifs_encrypted: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    antecedents_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    dfg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    grossesse: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    doctolib_patient_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )

    # ── Relations ──────────────────────────────────────────────────────────────
    cabinet: Mapped["Cabinet"] = relationship("Cabinet")
    consultations: Mapped[list["Consultation"]] = relationship(
        "Consultation", back_populates="patient"
    )
