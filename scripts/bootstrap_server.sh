#!/usr/bin/env bash
set -euo pipefail

# Bootstrap a fresh Ubuntu/Debian server for doc_helper.
#
# This script prepares infrastructure only. It does not restore a backup.
# Run as root or with sudo:
#
#   sudo REPO_URL='git@github.com:OWNER/REPO.git' \
#        DB_PASSWORD='...' \
#        DJANGO_SECRET_KEY='...' \
#        ./scripts/bootstrap_server.sh
#
# Optional variables:
#   PROJECT_DIR=/var/www/doc_helper
#   APP_USER=webapp
#   APP_GROUP=webapp
#   GUNICORN_GROUP=www-data
#   DOMAIN=doc-helper.pro
#   EXTRA_DOMAIN=www.doc-helper.pro
#   DB_NAME=doc_helper
#   DB_USER=doc_helper_user
#   SETUP_SSL=0
#   CERTBOT_EMAIL=admin@example.com

PROJECT_DIR="${PROJECT_DIR:-/var/www/doc_helper}"
APP_USER="${APP_USER:-webapp}"
APP_GROUP="${APP_GROUP:-webapp}"
GUNICORN_GROUP="${GUNICORN_GROUP:-www-data}"
DOMAIN="${DOMAIN:-doc-helper.pro}"
EXTRA_DOMAIN="${EXTRA_DOMAIN:-www.doc-helper.pro}"
DB_NAME="${DB_NAME:-doc_helper}"
DB_USER="${DB_USER:-doc_helper_user}"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
STORAGE_DIR="${DOC_HELPER_BASE_ID_DIR:-/var/lib/doc_helper/storage}"
BACKUP_DIR="${DOC_HELPER_BACKUP_DIR:-/var/lib/doc_helper/backups}"
ENV_DIR="${ENV_DIR:-/etc/doc_helper}"
ENV_FILE="${ENV_FILE:-/etc/doc_helper/doc_helper.env}"
SETUP_SSL="${SETUP_SSL:-0}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root or via sudo." >&2
    exit 1
  fi
}

require_value() {
  local name="$1"
  local value="${!name:-}"
  if [ -z "$value" ]; then
    echo "Required variable is empty: $name" >&2
    exit 1
  fi
}

shell_escape_single() {
  printf "%s" "$1" | sed "s/'/'\"'\"'/g"
}

install_packages() {
  apt-get update -y
  apt-get install -y \
    git \
    nginx \
    certbot \
    python3-certbot-nginx \
    postgresql \
    postgresql-client \
    python3 \
    python3-venv \
    python3-pip \
    libreoffice-calc \
    libreoffice-writer \
    unzip \
    rsync
}

create_user_and_dirs() {
  if ! id "$APP_USER" >/dev/null 2>&1; then
    adduser --system --group --home "$PROJECT_DIR" "$APP_USER"
  fi

  mkdir -p "$PROJECT_DIR" "$STORAGE_DIR" "$BACKUP_DIR" "$ENV_DIR"
  chown -R "$APP_USER:$APP_GROUP" "$PROJECT_DIR"
  chown -R "$APP_USER:$APP_GROUP" "$(dirname "$STORAGE_DIR")"
}

clone_or_update_repo() {
  require_value REPO_URL

  if [ -d "$PROJECT_DIR/.git" ]; then
    sudo -u "$APP_USER" git -C "$PROJECT_DIR" fetch --all
    sudo -u "$APP_USER" git -C "$PROJECT_DIR" pull --ff-only
    return
  fi

  if [ "$(find "$PROJECT_DIR" -mindepth 1 -maxdepth 1 | wc -l)" -ne 0 ]; then
    echo "$PROJECT_DIR is not empty and is not a git repository." >&2
    exit 1
  fi

  sudo -u "$APP_USER" git clone "$REPO_URL" "$PROJECT_DIR"
}

create_database() {
  require_value DB_PASSWORD

  local db_password_sql
  db_password_sql="$(printf "%s" "$DB_PASSWORD" | sed "s/'/''/g")"

  sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${DB_USER}') THEN
      CREATE ROLE ${DB_USER} LOGIN PASSWORD '${db_password_sql}';
   ELSE
      ALTER ROLE ${DB_USER} WITH PASSWORD '${db_password_sql}';
   END IF;
END
\$\$;
SQL

  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
    sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
  fi

  sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
ALTER ROLE ${DB_USER} SET client_encoding TO 'utf8';
ALTER ROLE ${DB_USER} SET default_transaction_isolation TO 'read committed';
ALTER ROLE ${DB_USER} SET timezone TO 'Europe/Moscow';
SQL
}

write_env_file() {
  require_value DJANGO_SECRET_KEY
  require_value DB_PASSWORD

  local allowed_hosts="${DJANGO_ALLOWED_HOSTS:-$DOMAIN,$EXTRA_DOMAIN,127.0.0.1,localhost}"

  cat > "$ENV_FILE" <<EOF
DJANGO_DEBUG='0'
DJANGO_SECRET_KEY='$(shell_escape_single "$DJANGO_SECRET_KEY")'
DJANGO_ALLOWED_HOSTS='$(shell_escape_single "$allowed_hosts")'
DOC_HELPER_BASE_ID_DIR='$(shell_escape_single "$STORAGE_DIR")'
DOC_HELPER_BACKUP_DIR='$(shell_escape_single "$BACKUP_DIR")'
DOC_HELPER_LIBREOFFICE_EXECUTABLE='libreoffice'
DOC_HELPER_LIBREOFFICE_TIMEOUT_SECONDS='180'
DB_NAME='$(shell_escape_single "$DB_NAME")'
DB_USER='$(shell_escape_single "$DB_USER")'
DB_PASSWORD='$(shell_escape_single "$DB_PASSWORD")'
DB_HOST='$(shell_escape_single "$DB_HOST")'
DB_PORT='$(shell_escape_single "$DB_PORT")'
S3_BACKUP_ENABLED='$(shell_escape_single "${S3_BACKUP_ENABLED:-false}")'
S3_BACKUP_ENDPOINT_URL='$(shell_escape_single "${S3_BACKUP_ENDPOINT_URL:-}")'
S3_BACKUP_REGION='$(shell_escape_single "${S3_BACKUP_REGION:-}")'
S3_BACKUP_BUCKET='$(shell_escape_single "${S3_BACKUP_BUCKET:-}")'
S3_BACKUP_ACCESS_KEY_ID='$(shell_escape_single "${S3_BACKUP_ACCESS_KEY_ID:-}")'
S3_BACKUP_SECRET_ACCESS_KEY='$(shell_escape_single "${S3_BACKUP_SECRET_ACCESS_KEY:-}")'
S3_BACKUP_PREFIX='$(shell_escape_single "${S3_BACKUP_PREFIX:-doc-helper-backups}")'
EOF

  chown "root:$APP_USER" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
}

install_python_dependencies() {
  sudo -u "$APP_USER" bash -lc "
    cd '$PROJECT_DIR'
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
  "
}

write_systemd_service() {
  cat > /etc/systemd/system/gunicorn-doc_helper.service <<EOF
[Unit]
Description=Gunicorn for doc_helper
After=network.target

[Service]
User=$APP_USER
Group=$GUNICORN_GROUP
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$PROJECT_DIR/venv/bin/gunicorn settings.wsgi:application \\
  --bind 127.0.0.1:8000 \\
  --workers 3 \\
  --timeout 120 \\
  --access-logfile - \\
  --error-logfile -

Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable gunicorn-doc_helper
}

write_nginx_site() {
  cat > /etc/nginx/sites-available/doc_helper <<EOF
server {
    listen 80;
    server_name $DOMAIN $EXTRA_DOMAIN;

    client_max_body_size 200m;

    location /static/ {
        alias $PROJECT_DIR/staticfiles/;
        expires 7d;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

  ln -sf /etc/nginx/sites-available/doc_helper /etc/nginx/sites-enabled/doc_helper
  nginx -t
  systemctl enable nginx
}

run_initial_django_checks() {
  sudo -u "$APP_USER" bash -lc "
    set -a
    source '$ENV_FILE'
    set +a
    cd '$PROJECT_DIR'
    source venv/bin/activate
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    python manage.py check
  "
}

setup_ssl_if_requested() {
  if [ "$SETUP_SSL" != "1" ]; then
    return
  fi

  if [ -z "$CERTBOT_EMAIL" ]; then
    echo "SETUP_SSL=1 requires CERTBOT_EMAIL." >&2
    exit 1
  fi

  certbot --nginx \
    -d "$DOMAIN" \
    -d "$EXTRA_DOMAIN" \
    --non-interactive \
    --agree-tos \
    --email "$CERTBOT_EMAIL" \
    --redirect
}

start_services() {
  systemctl restart gunicorn-doc_helper
  systemctl reload nginx
}

main() {
  require_root
  install_packages
  create_user_and_dirs
  clone_or_update_repo
  create_database
  write_env_file
  install_python_dependencies
  write_systemd_service
  write_nginx_site
  run_initial_django_checks
  setup_ssl_if_requested
  start_services

  echo "doc_helper bootstrap complete."
  echo "Project: $PROJECT_DIR"
  echo "Env: $ENV_FILE"
  echo "Domain: $DOMAIN $EXTRA_DOMAIN"
}

main "$@"
