#!/usr/bin/env bash
# provision_server.sh — Initialisation d'un serveur OVHcloud HDS MédecinAI
#
# À exécuter UNE SEULE FOIS en root sur un serveur Ubuntu 22.04 LTS fraîchement provisionné.
# Commande : curl -sSL https://raw.githubusercontent.com/.../provision_server.sh | sudo bash
#
# Ce script :
#   1. Durcit la configuration SSH
#   2. Configure le pare-feu (UFW)
#   3. Installe Docker + Docker Compose
#   4. Crée l'utilisateur applicatif
#   5. Installe node_exporter pour Prometheus
#   6. Initialise le répertoire de déploiement /opt/medecinai
#
# Variables à adapter avant exécution :
#   DEPLOY_USER   : nom de l'utilisateur applicatif (défaut : medecinai)
#   SSH_PUBKEY    : clé publique SSH autorisée (OBLIGATOIRE)
#   APP_ENV       : "staging" ou "production"

set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-medecinai}"
APP_ENV="${APP_ENV:-staging}"
APP_DIR="/opt/medecinai"
NODE_EXPORTER_VERSION="1.8.1"

# ── Couleurs ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { echo -e "\n${GREEN}[PROVISION]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

[[ $EUID -ne 0 ]] && { echo "Ce script doit être exécuté en root."; exit 1; }
[[ -z "${SSH_PUBKEY:-}" ]] && { echo "SSH_PUBKEY est obligatoire. Abandon."; exit 1; }

# ── 1. Mise à jour système ─────────────────────────────────────────────────────
step "Mise à jour du système..."
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq curl wget git unzip jq ufw fail2ban

# ── 2. Durcissement SSH ────────────────────────────────────────────────────────
step "Durcissement SSH..."
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/'         /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/'    /etc/ssh/sshd_config
sed -i 's/^#\?X11Forwarding.*/X11Forwarding no/'              /etc/ssh/sshd_config
sed -i 's/^#\?MaxAuthTries.*/MaxAuthTries 3/'                  /etc/ssh/sshd_config
echo "AllowUsers $DEPLOY_USER" >> /etc/ssh/sshd_config
systemctl reload sshd
echo "  SSH : connexion root désactivée, PasswordAuthentication désactivé"

# ── 3. Pare-feu UFW ──────────────────────────────────────────────────────────
step "Configuration UFW..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    comment "SSH"
ufw allow 80/tcp    comment "HTTP (redirection vers HTTPS)"
ufw allow 443/tcp   comment "HTTPS"
# Ports internes uniquement (pas exposés publiquement)
# 5432 PostgreSQL, 6379 Redis, 5672 RabbitMQ : accessibles via réseau privé OVHcloud uniquement
ufw --force enable
ufw status verbose
echo "  UFW activé : 22, 80, 443 ouverts. Ports DB/cache fermés publiquement."

# ── 4. Fail2ban ──────────────────────────────────────────────────────────────
step "Configuration fail2ban..."
cat > /etc/fail2ban/jail.local <<'EOF'
[sshd]
enabled = true
maxretry = 3
bantime  = 3600
findtime = 600
EOF
systemctl enable --now fail2ban

# ── 5. Docker ─────────────────────────────────────────────────────────────────
step "Installation de Docker..."
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
  echo "  Docker installé."
else
  echo "  Docker déjà installé."
fi

# Limiter les logs Docker pour éviter de saturer le disque
cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "5"
  },
  "live-restore": true
}
EOF
systemctl restart docker
echo "  Docker daemon configuré (log rotation, live-restore)."

# ── 6. Utilisateur applicatif ─────────────────────────────────────────────────
step "Création de l'utilisateur $DEPLOY_USER..."
if ! id "$DEPLOY_USER" &>/dev/null; then
  useradd -m -s /bin/bash -G docker "$DEPLOY_USER"
  echo "  Utilisateur $DEPLOY_USER créé."
else
  usermod -aG docker "$DEPLOY_USER"
  echo "  Utilisateur $DEPLOY_USER existant — ajouté au groupe docker."
fi

# Clé SSH autorisée
SSH_DIR="/home/$DEPLOY_USER/.ssh"
mkdir -p "$SSH_DIR"
echo "$SSH_PUBKEY" >> "$SSH_DIR/authorized_keys"
chmod 700 "$SSH_DIR"
chmod 600 "$SSH_DIR/authorized_keys"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$SSH_DIR"
echo "  Clé SSH configurée pour $DEPLOY_USER."

# ── 7. Répertoire de déploiement ──────────────────────────────────────────────
step "Initialisation du répertoire $APP_DIR..."
mkdir -p "$APP_DIR"
chown "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"

# Template .env (à remplir manuellement avec les secrets réels)
ENV_FILE="$APP_DIR/.env.${APP_ENV}"
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
# Généré par provision_server.sh — COMPLÉTER avec les valeurs réelles
# NE PAS committer ce fichier dans Git

APP_ENV=${APP_ENV}
GITHUB_REPOSITORY=a-maiza/medecin-ia

# Database (OVHcloud managed PostgreSQL)
DATABASE_URL=
DATABASE_SSL=true

# Redis (OVHcloud managed Redis)
REDIS_URL=

# RabbitMQ
RABBITMQ_USER=
RABBITMQ_PASSWORD=
CELERY_BROKER_URL=amqp://\${RABBITMQ_USER}:\${RABBITMQ_PASSWORD}@localhost:5672/medecinai
CELERY_RESULT_BACKEND=\${REDIS_URL}

# Auth0
AUTH0_DOMAIN=
AUTH0_CLIENT_ID=
AUTH0_CLIENT_SECRET=
AUTH0_AUDIENCE=
AUTH0_CALLBACK_URL=

# Anthropic
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-6

# Chiffrement patient — CHARGER DEPUIS HSM EN PROD
PATIENT_ENCRYPTION_MASTER_KEY=

# Application
APP_SECRET_KEY=
FRONTEND_URL=
BACKEND_URL=
ALLOWED_ORIGINS=

# Stripe
STRIPE_SECRET_KEY=
STRIPE_PUBLISHABLE_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_PRICE_SOLO=
STRIPE_PRICE_CABINET=
STRIPE_PRICE_RESEAU=

# Monitoring
GRAFANA_ADMIN_PASSWORD=
EOF
  chmod 600 "$ENV_FILE"
  chown "$DEPLOY_USER:$DEPLOY_USER" "$ENV_FILE"
  warn "Fichier $ENV_FILE créé — À REMPLIR avec les secrets réels avant démarrage !"
fi

# ── 8. node_exporter (métriques Prometheus) ──────────────────────────────────
step "Installation de node_exporter v${NODE_EXPORTER_VERSION}..."
if ! command -v node_exporter &>/dev/null; then
  ARCH=$(dpkg --print-architecture)
  [[ "$ARCH" == "amd64" ]] && ARCH="amd64" || ARCH="arm64"
  wget -q "https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/node_exporter-${NODE_EXPORTER_VERSION}.linux-${ARCH}.tar.gz" -O /tmp/node_exporter.tar.gz
  tar -xzf /tmp/node_exporter.tar.gz -C /tmp
  mv "/tmp/node_exporter-${NODE_EXPORTER_VERSION}.linux-${ARCH}/node_exporter" /usr/local/bin/
  rm -rf /tmp/node_exporter*

  # Systemd unit
  cat > /etc/systemd/system/node_exporter.service <<'EOF'
[Unit]
Description=Prometheus Node Exporter
After=network.target

[Service]
User=nobody
ExecStart=/usr/local/bin/node_exporter \
  --web.listen-address=127.0.0.1:9100 \
  --collector.systemd \
  --collector.processes
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now node_exporter
  echo "  node_exporter démarré sur 127.0.0.1:9100."
else
  echo "  node_exporter déjà installé."
fi

# ── Résumé ────────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo "  Provisioning $APP_ENV terminé."
echo ""
echo "  Prochaines étapes :"
echo "  1. Remplir $ENV_FILE avec les secrets réels"
echo "  2. Copier les fichiers docker-compose.${APP_ENV}.yml et"
echo "     le répertoire monitoring/ dans $APP_DIR/"
echo "  3. Ajouter les GitHub Secrets dans le dépôt :"
echo "     $(echo "$APP_ENV" | tr '[:lower:]' '[:upper:]')_SSH_HOST, _SSH_USER, _SSH_PRIVATE_KEY"
echo "  4. Lancer le premier déploiement en poussant sur main (staging)"
echo "     ou en créant un tag vX.Y.Z (prod)"
echo "========================================================"
