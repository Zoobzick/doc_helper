# doc_helper production server inventory

This document describes the current production shape captured from the live
server on 2026-07-09. It is the reference for disaster recovery bootstrap work.

## Server role

The server currently runs all production components on one VPS:

- Django application code and virtualenv
- Gunicorn application service
- Nginx reverse proxy
- PostgreSQL database
- Local media storage
- Local backup storage
- Let's Encrypt certificates

S3 is used as an off-server backup copy for ZIP backups.

## Operating system packages

Minimum package set for a fresh Ubuntu/Debian server:

```bash
git
nginx
certbot
python3-certbot-nginx
postgresql
postgresql-client
python3
python3-venv
python3-pip
libreoffice-calc
libreoffice-writer
unzip
rsync
```

`postgresql-client` is required for `pg_dump` and `pg_restore`.
LibreOffice is required for DOCX/XLSX to PDF conversion flows.

## Users and groups

Application system user:

```text
webapp
```

Gunicorn runs as:

```ini
User=webapp
Group=www-data
```

PostgreSQL roles on production:

```text
doc_helper_user
postgres
```

`postgres` is the PostgreSQL superuser. `doc_helper_user` owns the application
database.

## Filesystem layout

Project code and virtualenv:

```text
/var/www/doc_helper
/var/www/doc_helper/venv
/var/www/doc_helper/staticfiles
```

Application data:

```text
/var/lib/doc_helper/storage
/var/lib/doc_helper/backups
```

Configuration:

```text
/etc/doc_helper/doc_helper.env
/etc/systemd/system/gunicorn-doc_helper.service
/etc/nginx/sites-available/doc_helper
/etc/nginx/sites-enabled/doc_helper
```

Let's Encrypt:

```text
/etc/letsencrypt/live/doc-helper.pro/fullchain.pem
/etc/letsencrypt/live/doc-helper.pro/privkey.pem
```

## Ownership and permissions

Expected ownership:

```bash
sudo chown -R webapp:webapp /var/www/doc_helper
sudo chown -R webapp:webapp /var/lib/doc_helper
sudo chown root:webapp /etc/doc_helper/doc_helper.env
sudo chmod 640 /etc/doc_helper/doc_helper.env
```

The env file contains secrets. It should be readable by `root` and `webapp`,
but not by other users.

## Environment file

Production environment file:

```text
/etc/doc_helper/doc_helper.env
```

Required variables:

```env
DJANGO_DEBUG='0'
DJANGO_SECRET_KEY='...'
DJANGO_ALLOWED_HOSTS='doc-helper.pro,www.doc-helper.pro,127.0.0.1,localhost'
DOC_HELPER_BASE_ID_DIR='/var/lib/doc_helper/storage'
DOC_HELPER_BACKUP_DIR='/var/lib/doc_helper/backups'
DOC_HELPER_LIBREOFFICE_EXECUTABLE='libreoffice'
DOC_HELPER_LIBREOFFICE_TIMEOUT_SECONDS='180'
DB_NAME='doc_helper'
DB_USER='doc_helper_user'
DB_PASSWORD='...'
DB_HOST='127.0.0.1'
DB_PORT='5432'
S3_BACKUP_ENABLED='true'
S3_BACKUP_ENDPOINT_URL='https://s3.twcstorage.ru'
S3_BACKUP_REGION='ru-1'
S3_BACKUP_BUCKET='...'
S3_BACKUP_ACCESS_KEY_ID='...'
S3_BACKUP_SECRET_ACCESS_KEY='...'
S3_BACKUP_PREFIX='doc-helper-backups'
```

GitHub Secrets are used by the deploy workflow to recreate this env file on an
already prepared server. A brand-new server still needs bootstrap preparation
before normal deploy can work.

## PostgreSQL

Production database:

```text
Name: doc_helper
Owner: doc_helper_user
Encoding: UTF8
Collate: en_US.UTF-8
Ctype: en_US.UTF-8
Host: 127.0.0.1
Port: 5432
```

Current role list:

```text
doc_helper_user
postgres
```

The backup ZIP contains `db.dump` produced by `pg_dump -Fc`.

## Gunicorn systemd service

Service:

```text
gunicorn-doc_helper.service
```

Live service definition:

```ini
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
```

For the current 2 CPU / 4 GB RAM VPS, `--workers 3` is acceptable.

## Nginx

Enabled site symlink:

```text
/etc/nginx/sites-enabled/doc_helper -> /etc/nginx/sites-available/doc_helper
```

Domains:

```text
doc-helper.pro
www.doc-helper.pro
```

Nginx listens on HTTPS through Certbot-managed certificates.

Important live server block details:

```nginx
server_name doc-helper.pro www.doc-helper.pro;
client_max_body_size 200m;

location /static/ {
    alias /var/www/doc_helper/staticfiles/;
    expires 7d;
    add_header Cache-Control "public";
}

location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

`/media/` is currently not served directly by Nginx in the captured production
config. Do not enable direct media serving during bootstrap without a separate
security check.

## Backup layers

Current protection layers:

- local ZIP backup in `/var/lib/doc_helper/backups`
- S3 copy of ZIP backup
- VPS snapshot in the hosting provider panel
- code in GitHub
- secrets in GitHub Secrets and `/etc/doc_helper/doc_helper.env`

The ZIP backup contains database dump, media files, and `meta.json`. It does not
contain the full codebase, virtualenv, systemd config, Nginx config, PostgreSQL
installation, or OS packages.

## Fresh server bootstrap gap

The normal GitHub Actions deploy expects these things to already exist:

- `webapp` user
- `/var/www/doc_helper`
- cloned repository
- Python virtualenv
- PostgreSQL server, role, and database
- `/etc/doc_helper/doc_helper.env`
- `gunicorn-doc_helper.service`
- Nginx site config
- Certbot certificates or an HTTP config ready for Certbot

Use `scripts/bootstrap_server.sh` as the starting point for recreating this base
server state.
