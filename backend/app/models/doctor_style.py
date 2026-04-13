from typing import TYPE_CHECKING, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    from sqlalchemy import PickleType as Vector  # type: ignore[assignment]

if TYPE_CHECKING:
    from .medecin import Medecin


class DoctorStyleChunk(UUIDMixin, Base):
    """Chunks of validated SOAP notes used to learn the doctor's writing style (NS5)."""

    __tablename__ = "doctor_style_chunk"

    medecin_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("medecin.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(768), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )

    # ── Relations ──────────────────────────────────────────────────────────────
    medecin: Mapped["Medecin"] = relationship("Medecin", back_populates="style_chunks")
