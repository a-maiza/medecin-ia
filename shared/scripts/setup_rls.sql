-- setup_rls.sql
-- Row-Level Security policies for cabinet-level data isolation.
-- Called by setup_db.sh after schema migrations have run.
--
-- Session parameter: SET LOCAL app.current_cabinet_id = '<uuid>';
-- Must be set by the application before any query on RLS-protected tables.

-- ── chunk (all 5 namespaces) ──────────────────────────────────────────────────
ALTER TABLE chunk ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunk FORCE ROW LEVEL SECURITY;

-- Global namespaces (ccam, has, vidal): visible to all authenticated sessions
CREATE POLICY chunk_global_read ON chunk
    FOR SELECT
    USING (namespace IN ('ccam', 'has', 'vidal'));

-- patient_history: isolated per cabinet (via JSONB-derived patient_id → document → cabinet)
-- The generated column patient_id is derived from metadata; cabinet isolation is
-- enforced through the document table's cabinet_id (joined by document_id).
-- For performance, the application must pre-filter by patient_id from metadata.
CREATE POLICY chunk_patient_history_cabinet ON chunk
    FOR SELECT
    USING (
        namespace = 'patient_history'
        AND document_id IN (
            SELECT id FROM document
            WHERE cabinet_id = current_setting('app.current_cabinet_id', true)::uuid
        )
    );

-- doctor_corpus (NS5): isolated per cabinet via document table
CREATE POLICY chunk_doctor_corpus_cabinet ON chunk
    FOR SELECT
    USING (
        namespace = 'doctor_corpus'
        AND document_id IN (
            SELECT id FROM document
            WHERE cabinet_id = current_setting('app.current_cabinet_id', true)::uuid
        )
    );

-- Write access: restricted to current cabinet's documents only
CREATE POLICY chunk_insert_cabinet ON chunk
    FOR INSERT
    WITH CHECK (
        namespace IN ('ccam', 'has', 'vidal')   -- global: unrestricted write (admin only via role)
        OR document_id IN (
            SELECT id FROM document
            WHERE cabinet_id = current_setting('app.current_cabinet_id', true)::uuid
        )
    );

CREATE POLICY chunk_update_cabinet ON chunk
    FOR UPDATE
    USING (
        document_id IN (
            SELECT id FROM document
            WHERE cabinet_id = current_setting('app.current_cabinet_id', true)::uuid
        )
    );

CREATE POLICY chunk_delete_cabinet ON chunk
    FOR DELETE
    USING (
        document_id IN (
            SELECT id FROM document
            WHERE cabinet_id = current_setting('app.current_cabinet_id', true)::uuid
        )
    );

-- ── patient ───────────────────────────────────────────────────────────────────
ALTER TABLE patient ENABLE ROW LEVEL SECURITY;
ALTER TABLE patient FORCE ROW LEVEL SECURITY;

CREATE POLICY patient_cabinet_select ON patient
    FOR SELECT
    USING (
        cabinet_id = current_setting('app.current_cabinet_id', true)::uuid
    );

CREATE POLICY patient_cabinet_insert ON patient
    FOR INSERT
    WITH CHECK (
        cabinet_id = current_setting('app.current_cabinet_id', true)::uuid
    );

CREATE POLICY patient_cabinet_update ON patient
    FOR UPDATE
    USING (
        cabinet_id = current_setting('app.current_cabinet_id', true)::uuid
    );

CREATE POLICY patient_cabinet_delete ON patient
    FOR DELETE
    USING (
        cabinet_id = current_setting('app.current_cabinet_id', true)::uuid
    );

-- ── readonly_user bypass (SELECT-only role used by monitoring / analytics) ───
-- readonly_user is created by setup_db.sh with BYPASSRLS = false (intentional:
-- analytics must still see only the cabinet they're configured for).
-- If a superuser analytics role is needed, grant BYPASSRLS explicitly.

-- Allow the application's DB user to SET LOCAL app.current_cabinet_id
-- (required if the app connects as a non-superuser)
GRANT SET ON PARAMETER app.current_cabinet_id TO PUBLIC;
