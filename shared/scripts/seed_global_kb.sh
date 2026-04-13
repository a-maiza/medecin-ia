#!/usr/bin/env bash
# seed_global_kb.sh
# Seeds the global knowledge base by triggering Celery tasks in the correct order.
# Run once after the initial deployment (or after a full DB reset).
#
# Usage:
#   ./seed_global_kb.sh [--dry-run] [--skip-ccam] [--skip-has] [--skip-vidal]
#
# Requirements:
#   - CELERY_BROKER_URL environment variable set
#   - Backend container / venv active with celery CLI available
#   - Database migrations already applied

set -euo pipefail

DRY_RUN=false
SKIP_CCAM=false
SKIP_HAS=false
SKIP_VIDAL=false

for arg in "$@"; do
    case $arg in
        --dry-run)   DRY_RUN=true   ;;
        --skip-ccam) SKIP_CCAM=true ;;
        --skip-has)  SKIP_HAS=true  ;;
        --skip-vidal) SKIP_VIDAL=true ;;
        *) echo "Unknown argument: $arg" && exit 1 ;;
    esac
done

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

send_task() {
    local task_name="$1"
    local task_kwargs="${2:-{\}}"
    if $DRY_RUN; then
        log "[DRY-RUN] Would invoke Celery task: ${task_name} kwargs=${task_kwargs}"
        return 0
    fi
    log "Invoking Celery task: ${task_name} …"
    celery -A app.worker call "${task_name}" \
        --kwargs "${task_kwargs}" \
        --timeout 3600
}

wait_for_task() {
    local task_name="$1"
    local task_id="$2"
    log "Waiting for task ${task_name} (id=${task_id}) …"
    celery -A app.worker result "${task_id}" --timeout 7200
    log "Task ${task_name} completed."
}

# ── NS1: CCAM (Classification Commune des Actes Médicaux) ─────────────────────
if ! $SKIP_CCAM; then
    log "==> Step 1/3: Seeding CCAM (NS1) …"
    CCAM_ID=$(celery -A app.worker call kb.tasks.sync_ccam \
        --kwargs '{}' --timeout 3600 2>&1 | grep -oE '[0-9a-f-]{36}' | head -1 || true)
    if [ -n "${CCAM_ID:-}" ]; then
        wait_for_task "kb.tasks.sync_ccam" "$CCAM_ID"
    else
        log "WARN: Could not capture CCAM task ID — task may still be running."
    fi
fi

# ── NS2: HAS recommendations (memo technique only) ───────────────────────────
if ! $SKIP_HAS; then
    log "==> Step 2/3: Seeding HAS recommendations (NS2, memo-only) …"
    HAS_ID=$(celery -A app.worker call kb.tasks.sync_has \
        --kwargs '{"mode": "memo_only"}' --timeout 7200 2>&1 \
        | grep -oE '[0-9a-f-]{36}' | head -1 || true)
    if [ -n "${HAS_ID:-}" ]; then
        wait_for_task "kb.tasks.sync_has" "$HAS_ID"
    else
        log "WARN: Could not capture HAS task ID — task may still be running."
    fi
fi

# ── NS3: VIDAL (drug interactions & monographs) ───────────────────────────────
if ! $SKIP_VIDAL; then
    log "==> Step 3/3: Seeding VIDAL drug database (NS3) …"
    VIDAL_ID=$(celery -A app.worker call kb.tasks.sync_vidal \
        --kwargs '{}' --timeout 7200 2>&1 \
        | grep -oE '[0-9a-f-]{36}' | head -1 || true)
    if [ -n "${VIDAL_ID:-}" ]; then
        wait_for_task "kb.tasks.sync_vidal" "$VIDAL_ID"
    else
        log "WARN: Could not capture VIDAL task ID — task may still be running."
    fi
fi

log "Global knowledge base seeding complete."
log "Run 'celery -A app.worker inspect active' to verify no pending tasks remain."
