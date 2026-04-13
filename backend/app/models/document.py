from typing import TYPE_CHECKING, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Boolean, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin

if TYPE_CHECKING:
    from .chunk import Chunk

DocumentTypeEnum = Enum("global", "private", name="document_type_enum")
DocumentSourceEnum = Enum(
    "ccam", "has", "vidal", "cim10", "upload_medecin",
    name="document_source_enum",
)


class Document(UUIDMixin, Base):
    __tablename__ = "document"

    cabinet_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cabinet.id", ondelete="CASCADE"),
        nullable=True,   # null = document global
        index=True,
    )
    type: Mapped[str] = mapped_column(DocumentTypeEnum, nullable=False)
    source: Mapped[str] = mapped_column(DocumentSourceEnum, nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    content_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pathologie: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    specialite: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    annee: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    url_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    deprecated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    uploaded_by: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("medecin.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploaded_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )

    # ── Relations ──────────────────────────────────────────────────────────────
    chunks: Mapped[list["Chunk"]] = relationship("Chunk", back_populates="document")
