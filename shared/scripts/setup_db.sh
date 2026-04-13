#!/usr/bin/env bash
# setup_db.sh — Initialisation de la base de données MédecinAI
#
# Usage :
#   ./shared/scripts/setup_db.sh                  # utilise les valeurs du .env courant
#   DB_HOST=localhost DB_PORT=5432 ./setup_db.sh  # surcharge via env
#
# Prérequis : psql installé localement, conteneur Docker démarré (docker compose up -d)

set -euo pipefail

# ── Chargement .env ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env.dev"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -o allexport && source "$ENV_FILE" && set +o allexport
  echo "[setup_db] .env.dev chargé"
else
  echo "[setup_db] Avertissement : $ENV_FILE introuvable, utilisation des variables d'environnement existantes"
fi

# ── Variables (avec valeurs par défaut pour dev local) ───────────────────────
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-medecinai}"
DB_USER="${DB_USER:-app_user}"
DB_PASSWORD="${DB_PASSWORD:-dev_password}"
READONLY_PASSWORD="${READONLY_PASSWORD:-dev_readonly_password}"

export PGPASSWORD="$DB_PASSWORD"
PSQL="psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME"

# ── Attente disponibilité PostgreSQL ─────────────────────────────────────────
echo "[setup_db] Attente de PostgreSQL ($DB_HOST:$DB_PORT)..."
for i in $(seq 1 20); do
  if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -q; then
    echo "[setup_db] PostgreSQL disponible."
    break
  fi
  if [[ $i -eq 20 ]]; then
    echo "[setup_db] ERREUR : PostgreSQL inaccessible après 20 tentatives. Abandon." >&2
    exit 1
  fi
  sleep 2
done

# ── Extensions ────────────────────────────────────────────────────────────────
echo "[setup_db] Activation des extensions PostgreSQL..."
$PSQL <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
SQL
echo "[setup_db] Extensions : vector, pg_trgm, uuid-ossp — OK"

# ── Rôle readonly_user ────────────────────────────────────────────────────────
echo "[setup_db] Vérification du rôle readonly_user..."
$PSQL <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'readonly_user') THEN
    CREATE ROLE readonly_user WITH LOGIN PASSWORD '$READONLY_PASSWORD';
    RAISE NOTICE 'Rôle readonly_user créé.';
  ELSE
    RAISE NOTICE 'Rôle readonly_user déjà existant.';
  END IF;
END
\$\$;
GRANT CONNECT ON DATABASE $DB_NAME TO readonly_user;
SQL
echo "[setup_db] Rôle readonly_user — OK"

# ── Droits readonly après migrations ─────────────────────────────────────────
# Ce bloc est idempotent : à ré-exécuter après chaque `alembic upgrade head`
echo "[setup_db] Application des droits SELECT à readonly_user..."
$PSQL <<'SQL'
GRANT USAGE ON SCHEMA public TO readonly_user;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO readonly_user;
SQL
echo "[setup_db] Droits readonly_user — OK"

# ── Row-Level Security ────────────────────────────────────────────────────────
RLS_SCRIPT="$SCRIPT_DIR/setup_rls.sql"
if [[ -f "$RLS_SCRIPT" ]]; then
  echo "[setup_db] Application des policies RLS..."
  $PSQL -f "$RLS_SCRIPT"
  echo "[setup_db] RLS — OK"
else
  echo "[setup_db] Avertissement : $RLS_SCRIPT introuvable, RLS ignoré pour l'instant."
fi

echo ""
echo "[setup_db] Base de données prête."
