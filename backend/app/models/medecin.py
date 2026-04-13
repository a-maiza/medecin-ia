from typing import TYPE_CHECKING, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, UUIDMixin

if TYPE_CHECKING:
    from .cabinet import Cabinet
    from .consultation import Consultation
    from .doctor_style import DoctorStyleChunk
    from .metrics import ValidationMetric

RoleEnum = Enum(
    "medecin", "admin_cabinet", "admin_medecinai",
    name="medecin_role_enum",
)


class Medecin(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "medecin"

    cabinet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cabinet.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rpps: Mapped[str] = mapped_column(String(11), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    nom: Mapped[str] = mapped_column(String(100), nullable=False)
    prenom: Mapped[str] = mapped_column(String(100), nullable=False)
    specialite: Mapped[str] = mapped_column(String(100), nullable=False)
    auth0_sub: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(RoleEnum, nullable=False)
    preferences: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )

    # ── Relations ──────────────────────────────────────────────────────────────
    cabinet: Mapped["Cabinet"] = relationship(
        "Cabinet",
        back_populates="medecins",
        foreign_keys=[cabinet_id],
    )
    consultations: Mapped[list["Consultation"]] = relationship(
        "Consultation", back_populates="medecin"
    )
    style_chunks: Mapped[list["DoctorStyleChunk"]] = relationship(
        "DoctorStyleChunk", back_populates="medecin"
    )
    validation_metrics: Mapped[list["ValidationMetric"]] = relationship(
        "ValidationMetric", back_populates="medecin"
    )
