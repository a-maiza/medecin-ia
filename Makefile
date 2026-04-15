# ============================================================
#  MédecinAI — Makefile
#  Usage : make <target>
#  Cibles principales :
#    make dev      → démarrage complet (infra + backend + frontend)
#    make infra    → conteneurs Docker + base de données + migrations
#    make down     → arrêt et suppression des conteneurs
#    make clean    → arrêt + suppression des volumes (reset complet)
# ============================================================

SHELL := /bin/bash

# ── Couleurs ──────────────────────────────────────────────────────────────────
BOLD   := \033[1m
RESET  := \033[0m
GREEN  := \033[0;32m
YELLOW := \033[0;33m
CYAN   := \033[0;36m
RED    := \033[0;31m

# ── Répertoires & fichiers ────────────────────────────────────────────────────
ROOT_DIR      := $(shell pwd)
BACKEND_DIR   := $(ROOT_DIR)/backend
FRONTEND_DIR  := $(ROOT_DIR)/frontend
SCRIPTS_DIR   := $(ROOT_DIR)/shared/scripts
ENV_FILE      := $(ROOT_DIR)/.env.dev
COMPOSE_FILE  := $(ROOT_DIR)/docker-compose.dev.yml
VENV          := $(BACKEND_DIR)/venv
PYTHON        := $(VENV)/bin/python
PIP           := $(VENV)/bin/pip
UVICORN       := $(VENV)/bin/uvicorn
CELERY        := $(VENV)/bin/celery
ALEMBIC       := $(VENV)/bin/alembic

# ── Chargement .env.dev ───────────────────────────────────────────────────────
ifneq (,$(wildcard $(ENV_FILE)))
  include $(ENV_FILE)
  export
endif

# ── Valeurs par défaut ────────────────────────────────────────────────────────
BACKEND_PORT  ?= 8000
FRONTEND_PORT ?= 3000

# ─────────────────────────────────────────────────────────────────────────────
.PHONY: help dev infra up db migrate backend worker beat frontend \
        down stop clean logs health seed models install install-backend \
        install-frontend lint test

# ── Aide ──────────────────────────────────────────────────────────────────────
help: ## Affiche cette aide
	@echo ""
	@printf "  $(BOLD)MédecinAI — commandes disponibles$(RESET)\n\n"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-18s$(RESET) %s\n", $$1, $$2}'
	@echo ""

# ─────────────────────────────────────────────────────────────────────────────
#  CIBLES PRINCIPALES
# ─────────────────────────────────────────────────────────────────────────────

dev: ## 🚀  Démarrage complet : infra + backend + celery + frontend (tmux requis)
	@$(MAKE) infra
	@printf "\n$(BOLD)$(GREEN)Infra prête. Lancement des services applicatifs…$(RESET)\n\n"
	@if command -v tmux &>/dev/null; then \
		$(MAKE) _tmux_dev; \
	else \
		printf "$(YELLOW)tmux non trouvé — ouverture dans 3 terminaux séparés via osascript.$(RESET)\n"; \
		$(MAKE) _bg_dev; \
	fi

infra: check-env up wait-healthy db migrate ## 🏗  Conteneurs + DB + migrations
	@printf "\n$(BOLD)$(GREEN)✓ Infrastructure prête$(RESET)\n"
	@printf "  PostgreSQL  → localhost:5432\n"
	@printf "  Redis       → localhost:6379\n"
	@printf "  RabbitMQ    → localhost:5672  (UI: http://localhost:15672)\n\n"

# ─────────────────────────────────────────────────────────────────────────────
#  DOCKER COMPOSE
# ─────────────────────────────────────────────────────────────────────────────

up: ## 🐳  Démarre les conteneurs Docker (postgres, redis, rabbitmq)
	@printf "$(CYAN)▶ docker compose up$(RESET)\n"
	@docker compose -f $(COMPOSE_FILE) up -d --remove-orphans
	@printf "$(GREEN)✓ Conteneurs démarrés$(RESET)\n"

wait-healthy: ## ⏳  Attend que les conteneurs soient healthy
	@printf "$(CYAN)▶ Attente des healthchecks$(RESET)\n"
	@for svc in medecinai_postgres_dev medecinai_redis_dev medecinai_rabbitmq_dev; do \
		printf "  Attente $$svc "; \
		for i in $$(seq 1 30); do \
			status=$$(docker inspect --format='{{.State.Health.Status}}' $$svc 2>/dev/null || echo "absent"); \
			if [ "$$status" = "healthy" ]; then \
				printf " $(GREEN)✓$(RESET)\n"; break; \
			elif [ $$i -eq 30 ]; then \
				printf " $(RED)✗ timeout$(RESET)\n"; exit 1; \
			else \
				printf "."; sleep 2; \
			fi; \
		done; \
	done

down: ## 🛑  Arrête les conteneurs (conserve les volumes)
	@printf "$(CYAN)▶ docker compose down$(RESET)\n"
	@docker compose -f $(COMPOSE_FILE) down
	@printf "$(GREEN)✓ Conteneurs arrêtés$(RESET)\n"

stop: down ## Alias de down

clean: ## 🗑  Arrête les conteneurs ET supprime les volumes (reset complet)
	@printf "$(RED)▶ Suppression des conteneurs et volumes…$(RESET)\n"
	@docker compose -f $(COMPOSE_FILE) down -v --remove-orphans
	@printf "$(GREEN)✓ Nettoyage terminé$(RESET)\n"

logs: ## 📋  Affiche les logs des conteneurs (Ctrl-C pour quitter)
	@docker compose -f $(COMPOSE_FILE) logs -f

# ─────────────────────────────────────────────────────────────────────────────
#  BASE DE DONNÉES
# ─────────────────────────────────────────────────────────────────────────────

db: ## 🗄  Exécute setup_db.sh (extensions, rôles, RLS)
	@printf "$(CYAN)▶ setup_db.sh$(RESET)\n"
	@chmod +x $(SCRIPTS_DIR)/setup_db.sh
	@bash $(SCRIPTS_DIR)/setup_db.sh
	@printf "$(GREEN)✓ Base de données configurée$(RESET)\n"

migrate: check-venv ## 🔄  Alembic upgrade head
	@printf "$(CYAN)▶ alembic upgrade head$(RESET)\n"
	@cd $(BACKEND_DIR) && $(ALEMBIC) upgrade head
	@printf "$(GREEN)✓ Migrations appliquées$(RESET)\n"

migrate-new: check-venv ## 📝  Crée une nouvelle révision Alembic (MSG requis)
	@[ -n "$(MSG)" ] || (printf "$(RED)Usage : make migrate-new MSG='description'$(RESET)\n"; exit 1)
	@cd $(BACKEND_DIR) && $(ALEMBIC) revision --autogenerate -m "$(MSG)"

migrate-down: check-venv ## ⏪  Alembic downgrade -1
	@cd $(BACKEND_DIR) && $(ALEMBIC) downgrade -1
	@printf "$(GREEN)✓ Downgrade effectué$(RESET)\n"

# ─────────────────────────────────────────────────────────────────────────────
#  SERVICES APPLICATIFS
# ─────────────────────────────────────────────────────────────────────────────

backend: check-venv ## 🐍  Démarre FastAPI (uvicorn, reload activé)
	@printf "$(CYAN)▶ uvicorn — http://localhost:$(BACKEND_PORT)$(RESET)\n"
	@cd $(BACKEND_DIR) && $(UVICORN) app.main:app \
		--reload \
		--port $(BACKEND_PORT) \
		--host 0.0.0.0

worker: check-venv ## ⚙️   Démarre le worker Celery
	@printf "$(CYAN)▶ celery worker$(RESET)\n"
	@cd $(BACKEND_DIR) && $(CELERY) -A app.celery_app worker \
		--loglevel=info \
		--concurrency=2

beat: check-venv ## ⏰  Démarre Celery Beat (tâches planifiées)
	@printf "$(CYAN)▶ celery beat$(RESET)\n"
	@cd $(BACKEND_DIR) && $(CELERY) -A app.celery_app beat \
		--loglevel=info

frontend: check-node ## 🌐  Démarre Next.js (dev, port $(FRONTEND_PORT))
	@printf "$(CYAN)▶ Next.js — http://localhost:$(FRONTEND_PORT)$(RESET)\n"
	@cd $(FRONTEND_DIR) && npm run dev

# ─────────────────────────────────────────────────────────────────────────────
#  INSTALLATION
# ─────────────────────────────────────────────────────────────────────────────

install: install-backend install-frontend ## 📦  Installe toutes les dépendances

install-backend: ## 📦  Crée le venv Python et installe les dépendances backend
	@printf "$(CYAN)▶ Installation backend$(RESET)\n"
	@if [ ! -d "$(VENV)" ]; then \
		python3 -m venv $(VENV); \
		printf "$(GREEN)✓ Venv créé$(RESET)\n"; \
	fi
	@$(PIP) install --quiet --upgrade pip
	@$(PIP) install --quiet -r $(BACKEND_DIR)/requirements.txt
	@printf "$(GREEN)✓ Dépendances backend installées$(RESET)\n"

install-frontend: check-node ## 📦  Installe les dépendances frontend (npm install)
	@printf "$(CYAN)▶ npm install$(RESET)\n"
	@cd $(FRONTEND_DIR) && npm install --silent
	@printf "$(GREEN)✓ Dépendances frontend installées$(RESET)\n"

# ─────────────────────────────────────────────────────────────────────────────
#  QUALITÉ & TESTS
# ─────────────────────────────────────────────────────────────────────────────

lint: check-venv check-node ## 🔍  Lint (ruff + mypy + eslint)
	@printf "$(CYAN)▶ ruff$(RESET)\n"
	@cd $(BACKEND_DIR) && $(VENV)/bin/ruff check . || true
	@printf "$(CYAN)▶ mypy$(RESET)\n"
	@cd $(BACKEND_DIR) && $(VENV)/bin/mypy app/ --ignore-missing-imports || true
	@printf "$(CYAN)▶ eslint$(RESET)\n"
	@cd $(FRONTEND_DIR) && npm run lint || true

test: check-venv ## 🧪  Lance les tests backend (pytest)
	@printf "$(CYAN)▶ pytest$(RESET)\n"
	@cd $(BACKEND_DIR) && $(VENV)/bin/pytest tests/ -v

# ─────────────────────────────────────────────────────────────────────────────
#  HEALTH & SEEDS
# ─────────────────────────────────────────────────────────────────────────────

health: ## 💊  Vérifie l'état de tous les services
	@chmod +x $(SCRIPTS_DIR)/health_check.sh
	@bash $(SCRIPTS_DIR)/health_check.sh --no-gpu

seed: check-venv ## 🌱  Importe la base de connaissances globale (CCAM/HAS/VIDAL)
	@printf "$(CYAN)▶ seed_global_kb.sh$(RESET)\n"
	@chmod +x $(SCRIPTS_DIR)/seed_global_kb.sh
	@bash $(SCRIPTS_DIR)/seed_global_kb.sh

models: check-venv ## 🤖  Télécharge les modèles IA (Whisper, CamemBERT, reranker)
	@printf "$(CYAN)▶ Téléchargement des modèles IA$(RESET)\n"
	@$(PYTHON) -c "from sentence_transformers import SentenceTransformer; \
		print('  CamemBERT-bio…'); SentenceTransformer('almanach/camembert-bio')"
	@$(PYTHON) -c "from sentence_transformers import CrossEncoder; \
		print('  Cross-encoder…'); CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')"
	@$(PYTHON) -c "from faster_whisper import WhisperModel; \
		print('  Whisper large-v3…'); WhisperModel('large-v3', device='cpu', compute_type='int8')"
	@printf "$(GREEN)✓ Modèles téléchargés$(RESET)\n"

# ─────────────────────────────────────────────────────────────────────────────
#  GUARDS INTERNES
# ─────────────────────────────────────────────────────────────────────────────

check-env: ## Vérifie que .env.dev existe
	@if [ ! -f "$(ENV_FILE)" ]; then \
		printf "$(RED)✗ $(ENV_FILE) introuvable.$(RESET)\n"; \
		printf "  Copiez .env.example → .env.dev et remplissez les valeurs.\n\n"; \
		exit 1; \
	fi

check-venv: ## Vérifie que le venv Python existe
	@if [ ! -f "$(PYTHON)" ]; then \
		printf "$(YELLOW)⚠ Venv absent — lancement de make install-backend…$(RESET)\n"; \
		$(MAKE) install-backend; \
	fi

check-node: ## Vérifie que npm est installé
	@command -v npm &>/dev/null || \
		(printf "$(RED)✗ npm non trouvé. Installez Node.js >= 18.$(RESET)\n"; exit 1)

check-docker: ## Vérifie que Docker est lancé
	@docker info &>/dev/null || \
		(printf "$(RED)✗ Docker daemon injoignable. Démarrez Docker Desktop.$(RESET)\n"; exit 1)

# ─────────────────────────────────────────────────────────────────────────────
#  LANCEMENT PARALLÈLE (interne)
# ─────────────────────────────────────────────────────────────────────────────

_tmux_dev: ## Lance backend + worker + frontend dans des panes tmux
	@printf "$(CYAN)▶ Création session tmux 'medecinai'$(RESET)\n"
	@tmux new-session -d -s medecinai -n backend \
		"cd $(BACKEND_DIR) && $(UVICORN) app.main:app --reload --port $(BACKEND_PORT); bash" 2>/dev/null || true
	@tmux new-window -t medecinai -n worker \
		"cd $(BACKEND_DIR) && $(CELERY) -A app.celery_app worker --loglevel=info; bash"
	@tmux new-window -t medecinai -n frontend \
		"cd $(FRONTEND_DIR) && npm run dev; bash"
	@tmux select-window -t medecinai:backend
	@printf "\n$(BOLD)$(GREEN)✓ Tous les services démarrés dans tmux 'medecinai'$(RESET)\n"
	@printf "\n  $(BOLD)Commandes tmux :$(RESET)\n"
	@printf "  tmux attach -t medecinai    — attacher à la session\n"
	@printf "  Ctrl-b n / Ctrl-b p         — fenêtre suivante / précédente\n"
	@printf "  tmux kill-session -t medecinai — tout arrêter\n\n"
	@printf "  $(BOLD)URLs :$(RESET)\n"
	@printf "  API       → http://localhost:$(BACKEND_PORT)\n"
	@printf "  Docs API  → http://localhost:$(BACKEND_PORT)/docs\n"
	@printf "  Frontend  → http://localhost:$(FRONTEND_PORT)\n"
	@printf "  RabbitMQ  → http://localhost:15672\n\n"

_bg_dev: ## Lance les services en arrière-plan (logs dans /tmp/)
	@mkdir -p /tmp/medecinai-logs
	@printf "$(CYAN)▶ Démarrage backend en arrière-plan…$(RESET)\n"
	@cd $(BACKEND_DIR) && nohup $(UVICORN) app.main:app \
		--reload --port $(BACKEND_PORT) \
		> /tmp/medecinai-logs/backend.log 2>&1 & \
		echo $$! > /tmp/medecinai-logs/backend.pid
	@printf "$(CYAN)▶ Démarrage worker Celery en arrière-plan…$(RESET)\n"
	@cd $(BACKEND_DIR) && nohup $(CELERY) -A app.celery_app worker \
		--loglevel=info \
		> /tmp/medecinai-logs/worker.log 2>&1 & \
		echo $$! > /tmp/medecinai-logs/worker.pid
	@printf "$(CYAN)▶ Démarrage frontend en arrière-plan…$(RESET)\n"
	@cd $(FRONTEND_DIR) && nohup npm run dev \
		> /tmp/medecinai-logs/frontend.log 2>&1 & \
		echo $$! > /tmp/medecinai-logs/frontend.pid
	@sleep 3
	@printf "\n$(BOLD)$(GREEN)✓ Services démarrés en arrière-plan$(RESET)\n"
	@printf "\n  Logs : /tmp/medecinai-logs/\n"
	@printf "  Pour arrêter : $(BOLD)make stop-bg$(RESET)\n\n"
	@printf "  $(BOLD)URLs :$(RESET)\n"
	@printf "  API       → http://localhost:$(BACKEND_PORT)\n"
	@printf "  Docs API  → http://localhost:$(BACKEND_PORT)/docs\n"
	@printf "  Frontend  → http://localhost:$(FRONTEND_PORT)\n\n"

stop-bg: ## 🛑  Arrête les services lancés en arrière-plan (make _bg_dev)
	@for svc in backend worker frontend; do \
		pidfile=/tmp/medecinai-logs/$$svc.pid; \
		if [ -f $$pidfile ]; then \
			pid=$$(cat $$pidfile); \
			kill $$pid 2>/dev/null && printf "$(GREEN)✓ $$svc arrêté (PID $$pid)$(RESET)\n" || true; \
			rm -f $$pidfile; \
		fi; \
	done
	@$(MAKE) down

ps: ## 📊  Affiche l'état des conteneurs Docker
	@docker compose -f $(COMPOSE_FILE) ps

.DEFAULT_GOAL := help
