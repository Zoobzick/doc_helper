# Production Setup

Документ описывает два сценария:

- запуск production-сервера с нуля;
- запуск нового production-сервера из существующего бэкапа `doc-helper`.

Команды рассчитаны на Ubuntu/Debian, пользователя приложения `webapp`, проект в `/var/www/doc_helper` и env-файл `/etc/doc_helper/doc_helper.env`.

## 1. Подготовить сервер

```bash
sudo apt-get update -y
sudo apt-get upgrade -y

sudo apt-get install -y \
  git \
  nginx \
  postgresql \
  postgresql-client \
  python3 \
  python3-venv \
  python3-pip \
  libreoffice-calc \
  libreoffice-writer \
  unzip \
  rsync
```

Проверить:

```bash
python3 --version
pg_dump --version
libreoffice --version
```

## 2. Создать пользователя и директории

```bash
sudo adduser --system --group --home /var/www/doc_helper webapp

sudo mkdir -p /var/www/doc_helper
sudo mkdir -p /var/lib/doc_helper/storage
sudo mkdir -p /var/lib/doc_helper/backups
sudo mkdir -p /etc/doc_helper

sudo chown -R webapp:webapp /var/www/doc_helper
sudo chown -R webapp:webapp /var/lib/doc_helper
```

## 3. Получить код

```bash
sudo -u webapp git clone <REPO_URL> /var/www/doc_helper
cd /var/www/doc_helper
```

Если репозиторий уже склонирован:

```bash
sudo -u webapp git -C /var/www/doc_helper pull
```

## 4. Создать PostgreSQL БД

Заменить значения на реальные:

```bash
DB_NAME=doc_helper
DB_USER=doc_helper_user
DB_PASSWORD='strong_password_here'
```

Создать пользователя и БД:

```bash
sudo -u postgres psql <<SQL
CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';
CREATE DATABASE $DB_NAME OWNER $DB_USER;
ALTER ROLE $DB_USER SET client_encoding TO 'utf8';
ALTER ROLE $DB_USER SET default_transaction_isolation TO 'read committed';
ALTER ROLE $DB_USER SET timezone TO 'Europe/Moscow';
SQL
```

## 5. Создать production env

```bash
sudo tee /etc/doc_helper/doc_helper.env > /dev/null <<'EOF'
DJANGO_DEBUG='0'
DJANGO_SECRET_KEY='replace_me'
DJANGO_ALLOWED_HOSTS='example.com,127.0.0.1,localhost'
DOC_HELPER_BASE_ID_DIR='/var/lib/doc_helper/storage'
DOC_HELPER_BACKUP_DIR='/var/lib/doc_helper/backups'
DOC_HELPER_LIBREOFFICE_EXECUTABLE='libreoffice'
DOC_HELPER_LIBREOFFICE_TIMEOUT_SECONDS='180'
DB_NAME='doc_helper'
DB_USER='doc_helper_user'
DB_PASSWORD='strong_password_here'
DB_HOST='127.0.0.1'
DB_PORT='5432'
EOF

sudo chown root:webapp /etc/doc_helper/doc_helper.env
sudo chmod 640 /etc/doc_helper/doc_helper.env
```

Проверить:

```bash
sudo -u webapp bash -lc "set -a; source /etc/doc_helper/doc_helper.env; set +a; env | grep -E 'DJANGO|DOC_HELPER|DB_'"
```

## 6. Установить Python-зависимости

```bash
sudo -u webapp bash -lc "
  cd /var/www/doc_helper
  python3 -m venv venv
  source venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
"
```

## 7. Первый запуск с чистой БД

```bash
sudo -u webapp bash -lc "
  set -a
  source /etc/doc_helper/doc_helper.env
  set +a
  cd /var/www/doc_helper
  source venv/bin/activate
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
  python manage.py check
"
```

Создать администратора:

```bash
sudo -u webapp bash -lc "
  set -a
  source /etc/doc_helper/doc_helper.env
  set +a
  cd /var/www/doc_helper
  source venv/bin/activate
  python manage.py createsuperuser
"
```

## 8. systemd service

```bash
sudo tee /etc/systemd/system/gunicorn-doc_helper.service > /dev/null <<'EOF'
[Unit]
Description=Gunicorn for doc_helper
After=network.target

[Service]
User=webapp
Group=www-data
WorkingDirectory=/var/www/doc_helper
EnvironmentFile=/etc/doc_helper/doc_helper.env
ExecStart=/var/www/doc_helper/venv/bin/gunicorn settings.wsgi:application \
  --bind 127.0.0.1:8000 \
  --workers 3 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -

Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable gunicorn-doc_helper
sudo systemctl start gunicorn-doc_helper
sudo systemctl status gunicorn-doc_helper --no-pager
```

Для маленького сервера `1 CPU / 1 GB RAM` не увеличивайте workers/threads. Для `2 CPU / 4 GB RAM` можно оставить `--workers 3`, а threads добавлять только после наблюдения за нагрузкой.

## 9. Nginx

Пример:

```bash
sudo tee /etc/nginx/sites-available/doc_helper > /dev/null <<'EOF'
server {
    listen 80;
    server_name example.com;

    client_max_body_size 50M;

    location /static/ {
        alias /var/www/doc_helper/staticfiles/;
    }

    location /media/ {
        alias /var/lib/doc_helper/storage/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/doc_helper /etc/nginx/sites-enabled/doc_helper
sudo nginx -t
sudo systemctl reload nginx
```

## 10. Проверить production

```bash
curl -I http://127.0.0.1:8000/
sudo systemctl status gunicorn-doc_helper --no-pager
sudo systemctl status nginx --no-pager
df -h
free -h
```

В браузере проверить:

- вход в приложение;
- список актов;
- скачивание DOCX;
- страницу `/backups/`;
- создание ручного бэкапа.

## Запуск нового production-сервера из бэкапа

Этот сценарий нужен при переносе на новый сервер или восстановлении после сбоя.

### 1. Выполнить базовую подготовку

Сделать шаги:

- `1. Подготовить сервер`;
- `2. Создать пользователя и директории`;
- `3. Получить код`;
- `4. Создать PostgreSQL БД`;
- `5. Создать production env`;
- `6. Установить Python-зависимости`.

Миграции на пустую БД перед restore можно не выполнять, потому что БД будет восстановлена из dump.

### 2. Загрузить бэкап на сервер

Например:

```bash
sudo mkdir -p /var/lib/doc_helper/backups
sudo chown -R webapp:webapp /var/lib/doc_helper/backups
```

Скопировать архив в `/var/lib/doc_helper/backups`.

Ручные бэкапы называются `doc_helper_backup_*.zip`. Автоматический pre-deploy бэкап называется `doc_helper_deploy_latest.zip` и перезаписывается при каждом деплое.

### 3. Распаковать бэкап

```bash
BACKUP_ZIP=/var/lib/doc_helper/backups/doc_helper_deploy_latest.zip
# или:
# BACKUP_ZIP=/var/lib/doc_helper/backups/doc_helper_backup_YYYYMMDD_HHMMSS.zip
RESTORE_DIR=/tmp/doc_helper_restore

rm -rf "$RESTORE_DIR"
mkdir -p "$RESTORE_DIR"
unzip "$BACKUP_ZIP" -d "$RESTORE_DIR"

test -f "$RESTORE_DIR/db.dump"
test -d "$RESTORE_DIR/media"
cat "$RESTORE_DIR/meta.json"
```

### 4. Подгрузить env

```bash
set -a
source /etc/doc_helper/doc_helper.env
set +a
```

### 5. Восстановить БД

```bash
sudo -u postgres psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME';"
sudo -u postgres dropdb --if-exists "$DB_NAME"
sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"

PGPASSWORD="$DB_PASSWORD" pg_restore \
  --host="$DB_HOST" \
  --port="$DB_PORT" \
  --username="$DB_USER" \
  --dbname="$DB_NAME" \
  --no-owner \
  --clean \
  --if-exists \
  "$RESTORE_DIR/db.dump"
```

### 6. Восстановить файлы

```bash
sudo rm -rf "$DOC_HELPER_BASE_ID_DIR"
sudo mkdir -p "$DOC_HELPER_BASE_ID_DIR"
sudo rsync -a "$RESTORE_DIR/media/" "$DOC_HELPER_BASE_ID_DIR/"
sudo chown -R webapp:webapp "$DOC_HELPER_BASE_ID_DIR"
```

### 7. Вернуть код к нужному SHA

Открыть `meta.json`. Если там есть `deploy_new_sha`, обычно нужен он. Если восстанавливается состояние до неудачного деплоя, нужен `deploy_old_sha`.

```bash
cd /var/www/doc_helper
sudo -u webapp git fetch --all
sudo -u webapp git checkout <sha_from_meta_json>
```

### 8. Запустить сервисы

```bash
sudo systemctl daemon-reload
sudo systemctl enable gunicorn-doc_helper
sudo systemctl start gunicorn-doc_helper
sudo nginx -t
sudo systemctl reload nginx
```

### 9. Проверить

```bash
sudo -u webapp bash -lc "
  set -a
  source /etc/doc_helper/doc_helper.env
  set +a
  cd /var/www/doc_helper
  source venv/bin/activate
  python manage.py check
  python manage.py showmigrations
"
```

В браузере проверить критичные страницы и скачивание документов.
