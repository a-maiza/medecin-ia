# MédecinAI

Redonner 1h30 par jour à chaque médecin généraliste en automatisant la paperasse administrative.

Transcription audio temps réel → compte-rendu SOAP → codification CCAM/CIM-10 → export DMP/Doctolib.

---

## Démarrage local (développement)

### Prérequis

- Docker Desktop (ou Docker Engine + Compose v2)
- Python 3.12+
- Node.js 20+
- `psql` client (`brew install libpq` sur macOS)
- GPU CUDA optionnel — Whisper et les modèles d'embedding tournent sur CPU en dev (plus lent)

### 1. Variables d'environnement

```bash
cp .env.example .env.dev
# Éditer .env.dev : renseigner ANTHROPIC_API_KEY, AUTH0_*, PATIENT_ENCRYPTION_MASTER_KEY
```

Les seules valeurs obligatoires pour démarrer sont `ANTHROPIC_API_KEY` et les clés Auth0 dev.
Toutes les valeurs DB, Redis et RabbitMQ correspondent aux services Docker ci-dessous et sont
pré-remplies dans `.env.example`.

### 2. Services infrastructure

```bash
docker compose -f docker-compose.dev.yml up -d
```

Démarre PostgreSQL 16 + pgvector, Redis 7 et RabbitMQ 3.13.
RabbitMQ Management UI disponible sur http://localhost:15672 (dev_user / dev_password).

### 3. Base de données

```bash
# Initialise les extensions, les rôles et les policies RLS
bash shared/scripts/setup_db.sh

# Migrations Alembic (depuis le dossier backend, une fois créé)
cd backend
source venv/bin/activate
alembic upgrade head
```

### 4. Modèles IA (téléchargement unique)

```bash
cd backend
source venv/bin/activate

# Embedding MVP (CamemBERT-bio ~500 Mo)
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('almanach/camembert-bio')"

# Reranker (~120 Mo)
python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')"

# Whisper large-v3 (~3 Go) — utilise le CPU en dev si WHISPER_DEVICE=cpu
python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu', compute_type='int8')"
```

### 5. Backend FastAPI

```bash
cd backend
source venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

### 6. Frontend Next.js

```bash
cd frontend
npm install
npm run dev   # http://localhost:3000
```

### 7. Vérification

```bash
bash shared/scripts/health_check.sh --no-gpu
```

---

## Documentation

- Spécification complète : [Documents/REQUIREMENTS.md](Documents/REQUIREMENTS.md)
- Liste des tâches d'implémentation : [Documents/TASKS.md](Documents/TASKS.md)
- Guide Claude Code : [CLAUDE.md](CLAUDE.md)
