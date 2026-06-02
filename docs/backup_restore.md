# Backup and Restore

## Что входит в бэкап

Архив `doc_helper_backup_*.zip` содержит:

- `db.dump` — PostgreSQL dump в custom-формате `pg_dump -Fc`;
- `media/` — файлы из `MEDIA_ROOT`;
- `meta.json` — дата создания, trigger, git SHA, deploy old/new SHA, путь хранилища и количество файлов.

## Где лежат бэкапы

На production-сервере deploy workflow использует:

```bash
/var/lib/doc_helper/backups
```

По текущему `.github/workflows/deploy.yml` production env собирается из GitHub Secrets и записывается на сервер сюда:

```bash
/etc/doc_helper/doc_helper.env
```

Это не локальный `.env` из репозитория. Перед restore можно проверить, какой env-файл использует systemd-сервис:

```bash
sudo systemctl cat gunicorn-doc_helper
```

Локально путь можно задать через `.env`:

```env
DOC_HELPER_BACKUP_DIR=C:\dc_web\doc_helper\backups
```

## Создать бэкап вручную

```bash
cd /path/to/doc_helper
set -a
source /etc/doc_helper/doc_helper.env
set +a
source venv/bin/activate
python manage.py create_backup --trigger manual --reason "manual backup"
```

Перед миграциями deploy запускает файловый бэкап без записи в таблицу:

```bash
python manage.py create_backup --trigger deploy --reason "pre-migrate deploy backup" --no-db-record
```

## Восстановление сервера из бэкапа

Восстановление лучше делать вручную по SSH. Не запускайте restore из веб-интерфейса: приложение использует ту же БД, которую нужно заменить.

### 1. Остановить приложение

```bash
sudo systemctl stop gunicorn-doc_helper
```

Nginx можно оставить включённым, но пользователи будут получать ошибку до запуска приложения. Если нужно полностью закрыть доступ:

```bash
sudo systemctl stop nginx
```

### 2. Распаковать архив во временную папку

```bash
BACKUP_ZIP=/var/lib/doc_helper/backups/doc_helper_backup_YYYYMMDD_HHMMSS.zip
RESTORE_DIR=/tmp/doc_helper_restore

rm -rf "$RESTORE_DIR"
mkdir -p "$RESTORE_DIR"
unzip "$BACKUP_ZIP" -d "$RESTORE_DIR"
```

Проверить содержимое:

```bash
ls -lah "$RESTORE_DIR"
test -f "$RESTORE_DIR/db.dump"
test -d "$RESTORE_DIR/media"
cat "$RESTORE_DIR/meta.json"
```

Если это pre-deploy backup, в `meta.json` будут `deploy_old_sha` и `deploy_new_sha`. Для полного отката кода обычно нужен `deploy_old_sha`.

### 3. Подгрузить env

По текущему deploy workflow:

```bash
set -a
source /etc/doc_helper/doc_helper.env
set +a
```

Если `sudo systemctl cat gunicorn-doc_helper` показывает другой `EnvironmentFile`, используйте его.

### 4. Восстановить PostgreSQL

Завершить активные подключения к БД:

```bash
sudo -u postgres psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME';"
```

Пересоздать БД:

```bash
sudo -u postgres dropdb --if-exists "$DB_NAME"
sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
```

Восстановить dump:

```bash
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

### 5. Восстановить файлы хранилища

Текущий `MEDIA_ROOT` в production задаётся через `DOC_HELPER_BASE_ID_DIR`.

Сначала сохранить аварийную копию текущего состояния:

```bash
sudo -u webapp mv "$DOC_HELPER_BASE_ID_DIR" "${DOC_HELPER_BASE_ID_DIR}.before_restore_$(date +%Y%m%d_%H%M%S)"
sudo -u webapp mkdir -p "$DOC_HELPER_BASE_ID_DIR"
```

Скопировать файлы из бэкапа:

```bash
sudo rsync -a "$RESTORE_DIR/media/" "$DOC_HELPER_BASE_ID_DIR/"
sudo chown -R webapp:webapp "$DOC_HELPER_BASE_ID_DIR"
```

### 6. Проверить и запустить приложение

```bash
cd /path/to/doc_helper
git checkout <deploy_old_sha>
source venv/bin/activate
python manage.py check
python manage.py showmigrations
sudo systemctl start gunicorn-doc_helper
sudo systemctl start nginx
```

Проверить статус:

```bash
sudo systemctl status gunicorn-doc_helper --no-pager
sudo systemctl status nginx --no-pager
```

### 7. Проверить в браузере

- открыть главную страницу;
- открыть список актов;
- открыть страницу бэкапов;
- скачать один существующий акт/документ;
- проверить свежие данные, ради которых выполнялся restore.

## Важные замечания

- Перед restore желательно сохранить ещё один emergency backup текущего состояния, если диск и БД доступны.
- `db.dump` и `media/` должны восстанавливаться вместе: БД хранит ссылки на файлы.
- Если нужно откатить только код, БД можно не восстанавливать: достаточно переключить git на нужный SHA и перезапустить сервис.
- Если нужно откатить миграции данных, сначала оцените `python manage.py migrate app_name migration_name`; для серьёзных сбоев надёжнее полный restore из бэкапа.
