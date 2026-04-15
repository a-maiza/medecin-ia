# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project

**MédecinAI** — B2B SaaS médical qui transcrit les consultations en temps réel, génère des comptes-rendus SOAP, détecte les interactions médicamenteuses et répond aux questions cliniques via RAG. Cible : médecins généralistes libéraux français.

Source de vérité complète : `Documents/REQUIREMENTS.md`. Tâches d'implémentation : `Documents/TASKS.md`.

---

## Stack

| Couche | Technologie |
|---|---|
| Frontend | Next.js 14 App Router + TypeScript + Tailwind CSS + shadcn/ui |
| Backend | FastAPI (Python 3.12) + Pydantic + asyncpg |
| Base de données | PostgreSQL 16 + pgvector 0.7 |
| Cache / Queue | Redis 7 + Celery + RabbitMQ |
| Auth | Auth0 + Pro Santé Connect (e-CPS) |
| ASR | faster-whisper large-v3 (on-premise GPU) |
| LLM | Claude claude-sonnet-4-6 via Anthropic API (temperature=0.15) |
| Embedding | CamemBERT-bio 768d (MVP) → DrBERT-7GB-cased (V1) |
| Reranker | cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 |
| Hébergement | OVHcloud HDS (backend/DB/GPU) + Vercel Pro (frontend) |

---

## Commands

### Backend (FastAPI)

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Dev server (reload)
uvicorn app.main:app --reload --port 8000

# Run all tests
pytest

# Run a single test file
pytest tests/test_encryption.py -v

# Run a single test
pytest tests/test_encryption.py::test_round_trip -v

# Lint & type-check
ruff check .
mypy app/

# Migrations
alembic upgrade head
alembic revision --autogenerate -m "description"
alembic downgrade -1

# Celery worker
celery -A app.celery_app worker --loglevel=info

# Celery beat (scheduled jobs)
celery -A app.celery_app beat --loglevel=info
```

### Frontend (Next.js)

```bash
cd frontend
npm install

# Dev server
npm run dev

# Build
npm run build

# Lint
npm run lint

# Type-check
npx tsc --noEmit
```

### Database setup

```bash
# Enable extensions (run once)
psql -d medecinai -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql -d medecinai -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
psql -d medecinai -c "CREATE EXTENSION IF NOT EXISTS uuid-ossp;"

# RLS policies
psql -d medecinai -f shared/scripts/setup_rls.sql

# Run migrations
cd backend && alembic upgrade head

# Full stack health check
bash shared/scripts/health_check.sh
```

### AI models (download once)

```bash
cd backend
# Embedding (MVP)
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('almanach/camembert-bio')"
# Reranker
python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')"
# Whisper
python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu', compute_type='int8')"
```

### Knowledge base initial import

```bash
cd backend
python -m app.jobs.sync_ccam          # CCAM ATIH (~18min GPU)
python -m app.jobs.sync_has --mode=memo-only
python -m app.jobs.sync_vidal          # BDPM + Thériaque interactions
```

---

## Architecture

### Repository layout

```
/
├── frontend/          # Next.js 14 App Router
├── backend/           # FastAPI
│   └── app/
│       ├── routers/   # HTTP + WebSocket endpoints
│       ├── services/  # Business logic (soap_generator, patient_service…)
│       ├── models/    # SQLAlchemy ORM
│       ├── schemas/   # Pydantic I/O
│       ├── security/  # Encryption, pseudonymization, RLS, audit
│       └── jobs/      # Celery tasks (sync_ccam, sync_has, sync_vidal, index_document)
├── ia/                # AI modules, decoupled from HTTP layer
│   ├── rag/
│   │   ├── indexer/   # One indexer per namespace (CCAM, HAS, VIDAL, patient, doctor)
│   │   ├── retriever/ # hybrid_search, bm25_index, patient_store, query_enricher
│   │   └── reranker/  # cross_encoder, medical_booster, mmr
│   ├── transcription/ # whisper_pipeline, prompt_builder, postprocessor
│   ├── soap/          # prompt_assembler, output_validator, style_learner
│   └── prompts/       # System prompts as Python constants
├── knowledge-base/
│   ├── global/        # Import scripts for CCAM, HAS, VIDAL, CIM-10
│   └── private/       # Doctor uploads, isolated by cabinet_id (gitignored)
├── shared/
│   ├── types/         # TypeScript types shared with frontend
│   ├── constants/
│   └── scripts/       # setup_db.sh, setup_rls.sql, seed_global_kb.sh, health_check.sh
└── Documents/
    ├── REQUIREMENTS.md  # Source of truth — product + technical spec
    └── TASKS.md         # Implementation task list with P0/P1/P2 priorities
```

### RAG pipeline (5 namespaces in `chunks` table)

All vectors live in a single `chunks` table, discriminated by `source`:

| Namespace | `source` value | Isolation | Sync |
|---|---|---|---|
| NS1 CCAM | `ccam` | Global | Weekly |
| NS2 Guidelines HAS | `has` | Global | Monthly |
| NS3 Médicaments VIDAL | `vidal` | Global | Daily |
| NS4 Patient history | `patient_history` | `cabinet_id + patient_id` | Real-time |
| NS5 Doctor corpus | `doctor_corpus` | `doctor_id` | On each SOAP validation |

**Search flow:** query enrichment → parallel dense (HNSW pgvector top-20) + sparse (BM25Okapi top-20) → RRF fusion (k=60) → cross-encoder reranking (top-12 → top-5) → medical boosting (DFG, grossesse, interactions) → MMR deduplication → prompt assembly (6 layers) → Claude streaming → JSON schema validation.

**Critical isolation rule:** NS4 chunks must only be queried through `PatientVectorStore` (in `ia/rag/retriever/patient_store.py`) which hard-injects `patient_id + cabinet_id` filters. Never query `patient_history` chunks directly.

### SOAP generation flow

1. `POST /consultations` → create consultation record
2. WebSocket `/ws/transcription/{session_id}` → Whisper streaming + auto-save encrypted every 30s
3. `POST /soap/generate` → `soap_generator.py` orchestrates: interaction check (SQL, < 5ms) → RAG retrieval → prompt assembly → Claude stream → output validation
4. Frontend streams S/O/A/P sections as they arrive
5. `POST /soap/{id}/validate` → diff stored, NS5 indexed if quality_score > 0.7, e-CPS signature triggered

### Security constraints (non-negotiable)

- **All patient fields** (`nom`, `allergies`, `traitements_actifs`, `antecedents`, `transcript`) encrypted AES-256-GCM. Key derived via HKDF(master_key, patient_id). Master key from HSM in prod (env var only in dev).
- **Presidio pseudonymization** must run before any text is sent to the Anthropic API.
- **Row-Level Security** active on `chunks` and `patients`. Always call `rls.get_rls_context()` before queries on these tables.
- **`audit_log` is append-only** (PostgreSQL trigger prevents UPDATE/DELETE). Use `audit.log_event()` for: `soap_generated`, `soap_edited`, `alert_acknowledged`, `soap_signed`, `dmp_exported`, `doctolib_synced`, `export_failed`, `patient_data_accessed`, `document_uploaded`, `document_deleted`.
- **NS4 embeddings** must be computed on-premise (CamemBERT-bio / DrBERT). Never send patient text to an external embedding API.
- **CI_ABSOLUE interactions block SOAP generation entirely** (`soap: null`, alert only). CI_RELATIVE blocks until explicit clinical justification is recorded.

### Data model key points

- `Chunk.embedding` is `vector(768)` — matches both CamemBERT-bio and DrBERT dims.
- `Chunk.patient_id`, `doctor_id`, `specialty`, `has_grade` are PostgreSQL `GENERATED ALWAYS AS (metadata->>'field') STORED` columns — do not set them directly; populate `metadata` JSONB.
- `DrugInteraction` uses canonical pair ordering: always store with `drug_a < drug_b` (alphabetical). The CHECK constraint enforces this.
- `AuditLog.id` is `bigserial` (not UUID) for append ordering guarantees.

### LLM usage

- Model: `claude-sonnet-4-6`, temperature `0.15`, top_p `0.90`, max_tokens `1500`.
- Always stream responses (`stream=True`) — SOAP is rendered section by section in the UI.
- The SOAP output must be **pure JSON** matching the schema in `REQUIREMENTS.md §6`. Any non-JSON content is a validation failure.
- CCAM and CIM-10 codes must be traceable to a chunk in `chunks_used` — never invent codes.

## Git Workflow

When working on tasks listed in Tasks.md:

1. Before starting, create a new branch named `<type>/<task-number>-<brief-description>` from main,
   where the branch type reflects the nature of the change:
   - feat      → new features
   - fix       → bug fixes
   - docs      → documentation updates
   - test      → tests
   - refactor  → refactoring

2. Use atomic commits with conventional commit messages.

3. Once the task is finished, open a pull request that includes:
   - A title matching the task description
   - A brief summary of the changes made
   - Any relevant testing notes or considerations
   - An updated checkbox in TASKS.md to mark the task as complete

## Testing Requirements

Before marking any task as complete:

1. Write unit tests for new functionality
2. Run the full test suite
3. If tests fail:
   - Analyse the failure output
   - Fix the code (not the tests, unless the tests are incorrect)
   - Re-run tests until all pass