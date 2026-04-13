from typing import TYPE_CHECKING, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Computed, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # allow import without pgvector installed (CI type-check)
    from sqlalchemy import PickleType as Vector  # type: ignore[assignment]

if TYPE_CHECKING:
    from .document import Document

NamespaceEnum = Enum(
    "ccam", "has", "vidal", "patient_history", "doctor_corpus",
    name="chunk_namespace_enum",
)


class Chunk(UUIDMixin, Base):
    __tablename__ = "chunk"

    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    namespace: Mapped[str] = mapped_column(NamespaceEnum, nullable=False, index=True)

    text: Mapped[str] = mapped_column(Text, nullable=False)

    # chunk_metadata maps to column "metadata" (avoids SQLAlchemy reserved name)
    chunk_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    # ── GENERATED ALWAYS AS … STORED (derived from JSONB metadata) ────────────
    # SQLAlchemy Computed with persisted=True → PostgreSQL GENERATED ALWAYS AS
    patient_id: Mapped[Optional[str]] = mapped_column(
        PG_UUID(as_uuid=True),
        Computed("(metadata->>'patient_id')::uuid", persisted=True),
        nullable=True,
        index=True,
    )
    doctor_id: Mapped[Optional[str]] = mapped_column(
        PG_UUID(as_uuid=True),
        Computed("(metadata->>'doctor_id')::uuid", persisted=True),
        nullable=True,
        index=True,
    )
    specialty: Mapped[Optional[str]] = mapped_column(
        String(100),
        Computed("(metadata->>'specialty')", persisted=True),
        nullable=True,
        index=True,
    )
    has_grade: Mapped[Optional[str]] = mapped_column(
        String(10),
        Computed("(metadata->>'has_grade')", persisted=True),
        nullable=True,
    )

    # ── Vector embedding (768-dim, used by all 5 namespaces) ──────────────────
    embedding: Mapped[Optional[list]] = mapped_column(
        Vector(768), nullable=True
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )

    # ── Relations ──────────────────────────────────────────────────────────────
    document: Mapped["Document"] = relationship("Document", back_populates="chunks")
