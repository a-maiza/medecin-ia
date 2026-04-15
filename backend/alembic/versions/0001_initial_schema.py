"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00.000000

Creates all 12 tables, enums, indexes (including 7 HNSW partial indexes and 1 FTS GIN),
the circular FK between cabinet and medecin, GENERATED ALWAYS AS columns on chunk,
and the append-only trigger on audit_log.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── PostgreSQL extensions ─────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # ── Enums ─────────────────────────────────────────────────────────────────
    cabinet_plan_enum = postgresql.ENUM(
        "trial", "solo", "cabinet", "reseau",
        name="cabinet_plan_enum", create_type=False,
    )
    pays_enum = postgresql.ENUM("FR", "DZ", name="pays_enum", create_type=False)
    role_enum = postgresql.ENUM(
        "medecin", "admin_cabinet", "admin_medecinai",
        name="medecin_role_enum", create_type=False,
    )
    sexe_enum = postgresql.ENUM("M", "F", "autre", name="sexe_enum", create_type=False)
    consultation_status_enum = postgresql.ENUM(
        "in_progress", "generated", "validated", "exported",
        name="consultation_status_enum", create_type=False,
    )
    document_type_enum = postgresql.ENUM(
        "global", "private", name="document_type_enum", create_type=False,
    )
    document_source_enum = postgresql.ENUM(
        "ccam", "has", "vidal", "cim10", "upload_medecin",
        name="document_source_enum", create_type=False,
    )
    chunk_namespace_enum = postgresql.ENUM(
        "ccam", "has", "vidal", "patient_history", "doctor_corpus",
        name="chunk_namespace_enum", create_type=False,
    )
    drug_severity_enum = postgresql.ENUM(
        "contre_indication", "association_deconseille",
        "precaution_emploi", "a_prendre_en_compte",
        name="drug_interaction_severity_enum", create_type=False,
    )
    subscription_plan_enum = postgresql.ENUM(
        "trial", "solo", "cabinet", "reseau",
        name="subscription_plan_enum", create_type=False,
    )
    subscription_status_enum = postgresql.ENUM(
        "active", "past_due", "canceled", "unpaid",
        name="subscription_status_enum", create_type=False,
    )

    for enum in [
        cabinet_plan_enum, pays_enum, role_enum, sexe_enum,
        consultation_status_enum, document_type_enum, document_source_enum,
        chunk_namespace_enum, drug_severity_enum,
        subscription_plan_enum, subscription_status_enum,
    ]:
        enum.create(op.get_bind(), checkfirst=True)

    # ── cabinet (created before medecin; rpps_titulaire FK added after) ───────
    op.create_table(
        "cabinet",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("nom", sa.String(200), nullable=False),
        sa.Column("adresse", sa.Text, nullable=False),
        sa.Column("pays", pays_enum, nullable=False),
        sa.Column("siret", sa.String(14), nullable=True),
        sa.Column("rpps_titulaire", sa.String(11), nullable=True),  # FK added below
        sa.Column("stripe_customer_id", sa.String(100), nullable=True),
        sa.Column("plan", cabinet_plan_enum, nullable=False,
                  server_default="trial"),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # ── medecin ───────────────────────────────────────────────────────────────
    op.create_table(
        "medecin",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("cabinet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rpps", sa.String(11), nullable=False, unique=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("nom", sa.String(100), nullable=False),
        sa.Column("prenom", sa.String(100), nullable=False),
        sa.Column("specialite", sa.String(100), nullable=False),
        sa.Column("auth0_sub", sa.String(100), nullable=False, unique=True),
        sa.Column("role", role_enum, nullable=False),
        sa.Column("preferences", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["cabinet_id"], ["cabinet.id"],
                                ondelete="RESTRICT",
                                name="fk_medecin_cabinet_id"),
    )
    op.create_index("ix_medecin_cabinet_id", "medecin", ["cabinet_id"])

    # ── Circular FK: cabinet.rpps_titulaire → medecin.rpps ───────────────────
    op.create_foreign_key(
        "fk_cabinet_rpps_titulaire",
        "cabinet", "medecin",
        ["rpps_titulaire"], ["rpps"],
        ondelete="SET NULL",
        use_alter=True,
    )

    # ── patient ───────────────────────────────────────────────────────────────
    op.create_table(
        "patient",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("cabinet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ins", sa.String(22), nullable=True),
        sa.Column("nom_pseudonyme", sa.Text, nullable=False),
        sa.Column("date_naissance_hash", sa.String(64), nullable=False),
        sa.Column("sexe", sexe_enum, nullable=True),
        sa.Column("allergies_encrypted", sa.Text, nullable=True),
        sa.Column("traitements_actifs_encrypted", sa.Text, nullable=True),
        sa.Column("antecedents_encrypted", sa.Text, nullable=True),
        sa.Column("dfg", sa.Float, nullable=True),
        sa.Column("grossesse", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("doctolib_patient_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["cabinet_id"], ["cabinet.id"],
                                ondelete="RESTRICT",
                                name="fk_patient_cabinet_id"),
        sa.UniqueConstraint("cabinet_id", "ins", name="uq_patient_cabinet_ins"),
    )
    op.create_index("ix_patient_cabinet_id", "patient", ["cabinet_id"])

    # ── consultation ──────────────────────────────────────────────────────────
    op.create_table(
        "consultation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("cabinet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("medecin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("motif", sa.Text, nullable=False),
        sa.Column("transcript_encrypted", sa.Text, nullable=True),
        sa.Column("soap_generated", postgresql.JSONB, nullable=True),
        sa.Column("soap_validated", postgresql.JSONB, nullable=True),
        sa.Column("quality_score", sa.Float, nullable=True),
        sa.Column("correction_types", sa.ARRAY(sa.Text), nullable=True),
        sa.Column("status", consultation_status_enum, nullable=False,
                  server_default="in_progress"),
        sa.Column("alerts", postgresql.JSONB, nullable=True),
        sa.Column("chunks_used", sa.ARRAY(sa.Text), nullable=True),
        sa.Column("dmp_document_id", sa.String(100), nullable=True),
        sa.Column("doctolib_consultation_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["cabinet_id"], ["cabinet.id"],
                                ondelete="RESTRICT",
                                name="fk_consultation_cabinet_id"),
        sa.ForeignKeyConstraint(["medecin_id"], ["medecin.id"],
                                ondelete="RESTRICT",
                                name="fk_consultation_medecin_id"),
        sa.ForeignKeyConstraint(["patient_id"], ["patient.id"],
                                ondelete="RESTRICT",
                                name="fk_consultation_patient_id"),
    )
    op.create_index("ix_consultation_cabinet_id", "consultation", ["cabinet_id"])
    op.create_index("ix_consultation_medecin_id", "consultation", ["medecin_id"])
    op.create_index("ix_consultation_patient_id", "consultation", ["patient_id"])

    # ── document ──────────────────────────────────────────────────────────────
    op.create_table(
        "document",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("cabinet_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("type", document_type_enum, nullable=False),
        sa.Column("source", document_source_enum, nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("content_raw", sa.Text, nullable=True),
        sa.Column("pathologie", sa.String(200), nullable=True),
        sa.Column("specialite", sa.String(100), nullable=True),
        sa.Column("annee", sa.String(4), nullable=True),
        sa.Column("url_source", sa.Text, nullable=True),
        sa.Column("deprecated", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["cabinet_id"], ["cabinet.id"],
                                ondelete="CASCADE",
                                name="fk_document_cabinet_id"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["medecin.id"],
                                ondelete="SET NULL",
                                name="fk_document_uploaded_by"),
    )
    op.create_index("ix_document_cabinet_id", "document", ["cabinet_id"])

    # ── chunk (with GENERATED ALWAYS AS columns) ──────────────────────────────
    # GENERATED columns cannot be specified via Alembic column definitions —
    # we create the table with plain columns then ALTER to add generated columns.
    op.create_table(
        "chunk",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("namespace", chunk_namespace_enum, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column("embedding", sa.Text, nullable=True),  # placeholder, altered below
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"],
                                ondelete="CASCADE",
                                name="fk_chunk_document_id"),
    )
    op.create_index("ix_chunk_document_id", "chunk", ["document_id"])
    op.create_index("ix_chunk_namespace", "chunk", ["namespace"])

    # Drop placeholder embedding column and add real vector column
    op.drop_column("chunk", "embedding")
    op.execute("ALTER TABLE chunk ADD COLUMN embedding vector(768)")

    # Add GENERATED ALWAYS AS … STORED columns
    op.execute(
        "ALTER TABLE chunk "
        "ADD COLUMN patient_id UUID "
        "GENERATED ALWAYS AS ((metadata->>'patient_id')::uuid) STORED"
    )
    op.execute(
        "ALTER TABLE chunk "
        "ADD COLUMN doctor_id UUID "
        "GENERATED ALWAYS AS ((metadata->>'doctor_id')::uuid) STORED"
    )
    op.execute(
        "ALTER TABLE chunk "
        "ADD COLUMN specialty VARCHAR(100) "
        "GENERATED ALWAYS AS (metadata->>'specialty') STORED"
    )
    op.execute(
        "ALTER TABLE chunk "
        "ADD COLUMN has_grade VARCHAR(10) "
        "GENERATED ALWAYS AS (metadata->>'has_grade') STORED"
    )
    op.execute(
        "ALTER TABLE chunk "
        "ADD COLUMN cabinet_id UUID "
        "GENERATED ALWAYS AS ((metadata->>'cabinet_id')::uuid) STORED"
    )

    # ── drug_interaction ──────────────────────────────────────────────────────
    op.create_table(
        "drug_interaction",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("drug_a", sa.String(200), nullable=False),
        sa.Column("drug_b", sa.String(200), nullable=False),
        sa.Column("severity", drug_severity_enum, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("drug_a < drug_b", name="ck_drug_interaction_ordering"),
        sa.UniqueConstraint("drug_a", "drug_b", name="uq_drug_interaction_pair"),
    )
    op.create_index("ix_drug_interaction_severity", "drug_interaction", ["severity"])

    # ── doctor_style_chunk ────────────────────────────────────────────────────
    op.create_table(
        "doctor_style_chunk",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("medecin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["medecin_id"], ["medecin.id"],
                                ondelete="CASCADE",
                                name="fk_doctor_style_chunk_medecin_id"),
    )
    op.create_index("ix_doctor_style_chunk_medecin_id", "doctor_style_chunk",
                    ["medecin_id"])

    # Add vector embedding column (not supported by Alembic column spec)
    op.execute("ALTER TABLE doctor_style_chunk ADD COLUMN embedding vector(768)")

    # ── validation_metric ─────────────────────────────────────────────────────
    op.create_table(
        "validation_metric",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("consultation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("medecin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quality_score", sa.Float, nullable=True),
        sa.Column("correction_types", sa.ARRAY(sa.Text), nullable=True),
        sa.Column("time_to_validate_seconds", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["consultation_id"], ["consultation.id"],
                                ondelete="CASCADE",
                                name="fk_validation_metric_consultation_id"),
        sa.ForeignKeyConstraint(["medecin_id"], ["medecin.id"],
                                ondelete="RESTRICT",
                                name="fk_validation_metric_medecin_id"),
    )
    op.create_index("ix_validation_metric_consultation_id", "validation_metric",
                    ["consultation_id"])
    op.create_index("ix_validation_metric_medecin_id", "validation_metric",
                    ["medecin_id"])

    # ── training_pair ─────────────────────────────────────────────────────────
    op.create_table(
        "training_pair",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("consultation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("medecin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_soap", postgresql.JSONB, nullable=True),
        sa.Column("corrected_soap", postgresql.JSONB, nullable=True),
        sa.Column("diff_summary", sa.Text, nullable=True),
        sa.Column("tags", sa.ARRAY(sa.Text), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["consultation_id"], ["consultation.id"],
                                ondelete="CASCADE",
                                name="fk_training_pair_consultation_id"),
        sa.ForeignKeyConstraint(["medecin_id"], ["medecin.id"],
                                ondelete="RESTRICT",
                                name="fk_training_pair_medecin_id"),
    )

    # ── subscription ──────────────────────────────────────────────────────────
    op.create_table(
        "subscription",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("cabinet_id", postgresql.UUID(as_uuid=True), nullable=False,
                  unique=True),
        sa.Column("stripe_subscription_id", sa.String(100), nullable=True,
                  unique=True),
        sa.Column("plan", subscription_plan_enum, nullable=False),
        sa.Column("status", subscription_status_enum, nullable=False,
                  server_default="active"),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["cabinet_id"], ["cabinet.id"],
                                ondelete="CASCADE",
                                name="fk_subscription_cabinet_id"),
    )

    # ── audit_log ─────────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()"), index=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cabinet_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
    )
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_cabinet_id", "audit_log", ["cabinet_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])

    # Append-only trigger: prevent UPDATE and DELETE on audit_log
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_log_immutable()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only: % on audit_log is not allowed',
                TG_OP;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_log_immutable
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()
    """)

    # ── 7 HNSW partial indexes on chunk.embedding by namespace ────────────────
    # Parameters: m=16 (connections), ef_construction=64 (index build quality)
    # Using cosine distance (vector_cosine_ops) for sentence-transformer embeddings
    for ns in ("ccam", "has", "vidal", "patient_history", "doctor_corpus"):
        op.execute(
            f"CREATE INDEX ix_chunk_embedding_{ns} ON chunk "
            f"USING hnsw (embedding vector_cosine_ops) "
            f"WITH (m = 16, ef_construction = 64) "
            f"WHERE namespace = '{ns}'"
        )

    # Two additional partial indexes for patient isolation (NS4)
    op.execute(
        "CREATE INDEX ix_chunk_patient_isolation ON chunk (patient_id, cabinet_id) "
        "WHERE namespace = 'patient_history'"
    )
    op.execute(
        "CREATE INDEX ix_chunk_doctor_corpus ON chunk (doctor_id) "
        "WHERE namespace = 'doctor_corpus'"
    )

    # ── Full-text search GIN index on chunk.text ──────────────────────────────
    op.execute(
        "CREATE INDEX ix_chunk_fts ON chunk "
        "USING gin(to_tsvector('french', text))"
    )

    # ── HNSW index for doctor_style_chunk ─────────────────────────────────────
    op.execute(
        "CREATE INDEX ix_doctor_style_embedding ON doctor_style_chunk "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_immutable()")

    for table in [
        "audit_log", "training_pair", "validation_metric",
        "doctor_style_chunk", "drug_interaction", "chunk",
        "document", "consultation", "subscription", "patient",
    ]:
        op.drop_table(table)

    # Drop circular FK before dropping cabinet/medecin
    op.drop_constraint("fk_cabinet_rpps_titulaire", "cabinet", type_="foreignkey")
    op.drop_table("medecin")
    op.drop_table("cabinet")

    # Drop enums
    for enum_name in [
        "cabinet_plan_enum", "pays_enum", "medecin_role_enum", "sexe_enum",
        "consultation_status_enum", "document_type_enum", "document_source_enum",
        "chunk_namespace_enum", "drug_interaction_severity_enum",
        "subscription_plan_enum", "subscription_status_enum",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
