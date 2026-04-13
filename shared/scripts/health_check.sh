#!/usr/bin/env bash
# health_check.sh — Vérification de la stack MédecinAI
#
# Usage :
#   ./shared/scripts/health_check.sh          # vérifie tout
#   ./shared/scripts/health_check.sh --no-gpu # ignore la vérification GPU
#
# Retourne 0 si tous les services obligatoires sont OK, 1 sinon.

set -euo pipefail

# ── Chargement .env ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env.dev"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -o allexport && source "$ENV_FILE" && set +o allexport
fi

# ── Options ───────────────────────────────────────────────────────────────────
SKIP_GPU=false
for arg in "$@"; do
  [[ "$arg" == "--no-gpu" ]] && SKIP_GPU=true
done

# ── Helpers ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

ERRORS=0

ok()   { echo -e "  ${GREEN}[OK]${NC}    $*"; }
fail() { echo -e "  ${RED}[FAIL]${NC}  $*"; ERRORS=$((ERRORS + 1)); }
warn() { echo -e "  ${YELLOW}[WARN]${NC}  $*"; }

# ── Valeurs par défaut ────────────────────────────────────────────────────────
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-app_user}"
DB_PASSWORD="${DB_PASSWORD:-dev_password}"
DB_NAME="${DB_NAME:-medecinai}"

REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"

RABBITMQ_HOST="${RABBITMQ_HOST:-localhost}"
RABBITMQ_PORT="${RABBITMQ_PORT:-5672}"
RABBITMQ_MGMT_PORT="${RABBITMQ_MGMT_PORT:-15672}"

BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"

echo ""
echo "=== MédecinAI — Health Check ==="
echo ""

# ── PostgreSQL ────────────────────────────────────────────────────────────────
echo "PostgreSQL ($DB_HOST:$DB_PORT)"
if command -v pg_isready &>/dev/null; then
  if PGPASSWORD="$DB_PASSWORD" pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -q; then
    ok "Connexion PostgreSQL"

    # Vérification extensions
    EXTENSIONS=$(PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc \
      "SELECT string_agg(extname, ', ' ORDER BY extname) FROM pg_extension WHERE extname IN ('vector','pg_trgm','uuid-ossp');" 2>/dev/null || echo "")
    if [[ "$EXTENSIONS" == *"vector"* ]]; then
      ok "Extension pgvector"
    else
      fail "Extension pgvector manquante — exécuter setup_db.sh"
    fi
    if [[ "$EXTENSIONS" == *"pg_trgm"* ]]; then
      ok "Extension pg_trgm"
    else
      fail "Extension pg_trgm manquante — exécuter setup_db.sh"
    fi
  else
    fail "PostgreSQL inaccessible sur $DB_HOST:$DB_PORT"
  fi
else
  warn "pg_isready introuvable — vérification PostgreSQL ignorée (installer postgresql-client)"
fi

echo ""

# ── Redis ─────────────────────────────────────────────────────────────────────
echo "Redis ($REDIS_HOST:$REDIS_PORT)"
if command -v redis-cli &>/dev/null; then
  if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null | grep -q PONG; then
    ok "Connexion Redis"
  else
    fail "Redis inaccessible sur $REDIS_HOST:$REDIS_PORT"
  fi
else
  # Fallback via /dev/tcp si redis-cli absent
  if (echo -e "*1\r\n\$4\r\nPING\r\n" | timeout 3 bash -c "cat > /dev/tcp/$REDIS_HOST/$REDIS_PORT" 2>/dev/null); then
    ok "Redis port ouvert (redis-cli absent pour vérification complète)"
  else
    fail "Redis inaccessible sur $REDIS_HOST:$REDIS_PORT"
  fi
fi

echo ""

# ── RabbitMQ ──────────────────────────────────────────────────────────────────
echo "RabbitMQ ($RABBITMQ_HOST:$RABBITMQ_PORT)"
if timeout 3 bash -c "echo > /dev/tcp/$RABBITMQ_HOST/$RABBITMQ_PORT" 2>/dev/null; then
  ok "Port AMQP ouvert"
else
  fail "RabbitMQ inaccessible sur $RABBITMQ_HOST:$RABBITMQ_PORT"
fi

if timeout 3 bash -c "echo > /dev/tcp/$RABBITMQ_HOST/$RABBITMQ_MGMT_PORT" 2>/dev/null; then
  ok "Management UI accessible sur :$RABBITMQ_MGMT_PORT"
else
  warn "Management UI RabbitMQ inaccessible sur :$RABBITMQ_MGMT_PORT (non bloquant)"
fi

echo ""

# ── GPU (optionnel en dev) ────────────────────────────────────────────────────
echo "GPU / CUDA"
if [[ "$SKIP_GPU" == true ]]; then
  warn "Vérification GPU ignorée (--no-gpu)"
elif command -v nvidia-smi &>/dev/null; then
  if nvidia-smi --query-gpu=name --format=csv,noheader &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    ok "GPU détecté : $GPU_NAME"
  else
    warn "nvidia-smi présent mais aucun GPU détecté (mode CPU pour Whisper/embedding)"
  fi
else
  warn "nvidia-smi absent — Whisper et embedding tourneront sur CPU (plus lent)"
fi

echo ""

# ── Backend FastAPI ───────────────────────────────────────────────────────────
echo "Backend FastAPI ($BACKEND_URL)"
if command -v curl &>/dev/null; then
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BACKEND_URL/health" 2>/dev/null || echo "000")
  if [[ "$HTTP_CODE" == "200" ]]; then
    ok "GET /health → 200"
  elif [[ "$HTTP_CODE" == "000" ]]; then
    warn "Backend non démarré (normal si pas encore lancé)"
  else
    fail "GET /health → HTTP $HTTP_CODE"
  fi
else
  warn "curl absent — vérification backend ignorée"
fi

echo ""

# ── Frontend Next.js ──────────────────────────────────────────────────────────
echo "Frontend Next.js ($FRONTEND_URL)"
if command -v curl &>/dev/null; then
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$FRONTEND_URL" 2>/dev/null || echo "000")
  if [[ "$HTTP_CODE" =~ ^(200|307|308)$ ]]; then
    ok "Frontend accessible → HTTP $HTTP_CODE"
  elif [[ "$HTTP_CODE" == "000" ]]; then
    warn "Frontend non démarré (normal si pas encore lancé)"
  else
    fail "Frontend → HTTP $HTTP_CODE"
  fi
else
  warn "curl absent — vérification frontend ignorée"
fi

echo ""

# ── Résumé ────────────────────────────────────────────────────────────────────
echo "================================="
if [[ $ERRORS -eq 0 ]]; then
  echo -e "${GREEN}Tous les services obligatoires sont opérationnels.${NC}"
  exit 0
else
  echo -e "${RED}$ERRORS service(s) en erreur — voir détails ci-dessus.${NC}"
  exit 1
fi
