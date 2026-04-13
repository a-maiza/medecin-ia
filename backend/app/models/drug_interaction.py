import sqlalchemy as sa
from sqlalchemy import CheckConstraint, Enum, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin

SeverityEnum = Enum(
    "contre_indication", "association_deconseille", "precaution_emploi", "a_prendre_en_compte",
    name="drug_interaction_severity_enum",
)


class DrugInteraction(UUIDMixin, Base):
    __tablename__ = "drug_interaction"
    __table_args__ = (
        # Canonical ordering: drug_a < drug_b prevents duplicate reversed pairs
        CheckConstraint("drug_a < drug_b", name="ck_drug_interaction_ordering"),
        UniqueConstraint("drug_a", "drug_b", name="uq_drug_interaction_pair"),
    )

    # Normalised DCI names (lowercase), canonical ordering enforced by CHECK
    drug_a: Mapped[str] = mapped_column(String(200), nullable=False)
    drug_b: Mapped[str] = mapped_column(String(200), nullable=False)

    severity: Mapped[str] = mapped_column(SeverityEnum, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "vidal", "has"

    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )
