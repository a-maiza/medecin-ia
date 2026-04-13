from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .medecin import Medecin
    from .subscription import Subscription

CabinetPlanEnum = Enum(
    "trial", "solo", "cabinet", "reseau",
    name="cabinet_plan_enum",
)

PaysEnum = Enum("FR", "DZ", name="pays_enum")


class Cabinet(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "cabinet"

    nom: Mapped[str] = mapped_column(String(200), nullable=False)
    adresse: Mapped[str] = mapped_column(Text, nullable=False)
    pays: Mapped[str] = mapped_column(PaysEnum, nullable=False)
    siret: Mapped[Optional[str]] = mapped_column(String(14), nullable=True)

    # FK circulaire vers Medecin.rpps — contrainte ajoutée après création de medecin
    rpps_titulaire: Mapped[Optional[str]] = mapped_column(
        String(11),
        sa.ForeignKey("medecin.rpps", use_alter=True, name="fk_cabinet_rpps_titulaire"),
        nullable=True,   # nullable pendant le bootstrap (médecin créé après cabinet)
    )

    stripe_customer_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    plan: Mapped[str] = mapped_column(
        CabinetPlanEnum, nullable=False, server_default="trial"
    )
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    # ── Relations ──────────────────────────────────────────────────────────────
    medecins: Mapped[list["Medecin"]] = relationship(
        "Medecin",
        back_populates="cabinet",
        foreign_keys="Medecin.cabinet_id",
    )
    subscription: Mapped[Optional["Subscription"]] = relationship(
        "Subscription", back_populates="cabinet", uselist=False
    )
