from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections, connections
from django.utils import timezone

from backup_app.models import BackupRun


@dataclass(frozen=True)
class BackupResult:
    run: BackupRun | None
    path: Path
    size_bytes: int
    s3_key: str = ""


def get_backup_root() -> Path:
    root = getattr(settings, "BACKUP_ROOT", None)
    return Path(root) if root else Path(settings.BASE_DIR) / "backups"


def is_s3_backup_enabled() -> bool:
    return bool(getattr(settings, "S3_BACKUP_ENABLED", False))


def _build_s3_key(archive_path: Path) -> str:
    prefix = (getattr(settings, "S3_BACKUP_PREFIX", "") or "").strip().strip("/")
    if prefix:
        return f"{prefix}/{archive_path.name}"
    return archive_path.name


def get_s3_marker_path(archive_path: Path) -> Path:
    return archive_path.with_name(f"{archive_path.name}.s3.json")


def write_s3_marker(archive_path: Path, s3_key: str) -> None:
    if not s3_key:
        return
    marker_path = get_s3_marker_path(archive_path)
    payload = {
        "bucket": (getattr(settings, "S3_BACKUP_BUCKET", "") or "").strip(),
        "key": s3_key,
        "uploaded_at": timezone.now().isoformat(),
    }
    marker_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_s3_backup_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("S3 backup is enabled, but boto3 is not installed.") from exc

    bucket = (getattr(settings, "S3_BACKUP_BUCKET", "") or "").strip()
    if not bucket:
        raise RuntimeError("S3_BACKUP_BUCKET is required when S3 backup is enabled.")

    endpoint_url = (getattr(settings, "S3_BACKUP_ENDPOINT_URL", "") or "").strip() or None
    region_name = (getattr(settings, "S3_BACKUP_REGION", "") or "").strip() or None
    access_key_id = (getattr(settings, "S3_BACKUP_ACCESS_KEY_ID", "") or "").strip() or None
    secret_access_key = (getattr(settings, "S3_BACKUP_SECRET_ACCESS_KEY", "") or "").strip() or None
    if not access_key_id or not secret_access_key:
        raise RuntimeError("S3 backup credentials are required when S3 backup is enabled.")

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region_name,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )


def upload_backup_to_s3(archive_path: Path) -> str:
    if not is_s3_backup_enabled():
        return ""

    bucket = (getattr(settings, "S3_BACKUP_BUCKET", "") or "").strip()
    if not bucket:
        raise RuntimeError("S3_BACKUP_BUCKET is required when S3 backup is enabled.")

    key = _build_s3_key(archive_path)
    client = _get_s3_backup_client()
    client.upload_file(str(archive_path), bucket, key)
    return key


def delete_backup_from_s3(key: str) -> None:
    if not is_s3_backup_enabled() or not key:
        return

    bucket = (getattr(settings, "S3_BACKUP_BUCKET", "") or "").strip()
    if not bucket:
        raise RuntimeError("S3_BACKUP_BUCKET is required when S3 backup is enabled.")

    client = _get_s3_backup_client()
    client.delete_object(Bucket=bucket, Key=key)


def _finish_backup_result(*, run: BackupRun | None, path: Path, size_bytes: int) -> BackupResult:
    s3_key = upload_backup_to_s3(path)
    write_s3_marker(path, s3_key)
    return BackupResult(run=run, path=path, size_bytes=size_bytes, s3_key=s3_key)


def _timestamp() -> str:
    return timezone.localtime().strftime("%Y%m%d_%H%M%S")


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(settings.BASE_DIR), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def _dump_postgresql_database(output_path: Path) -> None:
    db = connections["default"].settings_dict
    env = os.environ.copy()
    password = db.get("PASSWORD") or ""
    if password:
        env["PGPASSWORD"] = password

    pg_dump = os.environ.get("PG_DUMP_PATH") or shutil.which("pg_dump")
    if not pg_dump:
        raise RuntimeError(
            "Не найден pg_dump. Добавьте папку bin PostgreSQL в PATH "
            "или укажите полный путь в переменной PG_DUMP_PATH."
        )

    command = [
        pg_dump,
        "-Fc",
        "-f",
        str(output_path),
        "-h",
        str(db.get("HOST") or "localhost"),
        "-p",
        str(db.get("PORT") or "5432"),
        "-U",
        str(db.get("USER") or ""),
        str(db.get("NAME") or ""),
    ]
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "pg_dump failed").strip()
        raise RuntimeError(message)


def _dump_sqlite_database(output_path: Path) -> None:
    db_path = Path(connections["default"].settings_dict["NAME"])
    shutil.copy2(db_path, output_path)


def _dump_database(output_dir: Path) -> Path:
    engine = connections["default"].settings_dict["ENGINE"]
    if engine.endswith("postgresql"):
        output_path = output_dir / "db.dump"
        _dump_postgresql_database(output_path)
        return output_path
    if engine.endswith("sqlite3"):
        output_path = output_dir / "db.sqlite3"
        _dump_sqlite_database(output_path)
        return output_path
    raise RuntimeError(f"Неподдерживаемая БД для бэкапа: {engine}")


def _write_media_files(archive: zipfile.ZipFile) -> int:
    media_root = Path(settings.MEDIA_ROOT)
    if not media_root.exists():
        return 0

    backup_root = get_backup_root().resolve()
    files_count = 0
    for path in media_root.rglob("*"):
        if not path.is_file():
            continue

        resolved = path.resolve()
        if resolved == backup_root or backup_root in resolved.parents:
            continue

        archive.write(path, (Path("media") / path.relative_to(media_root)).as_posix())
        files_count += 1

    return files_count


def _write_meta(
    output_path: Path,
    *,
    trigger: str,
    reason: str,
    run_id: int | None = None,
    media_files_count: int = 0,
) -> None:
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "trigger": trigger,
        "run_id": run_id,
        "reason": reason,
        "git_sha": _git_sha(),
        "deploy_old_sha": os.environ.get("DOC_HELPER_DEPLOY_OLD_SHA", ""),
        "deploy_new_sha": os.environ.get("DOC_HELPER_DEPLOY_NEW_SHA", ""),
        "database_engine": connections["default"].settings_dict["ENGINE"],
        "media_root": str(settings.MEDIA_ROOT),
        "media_files_count": media_files_count,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _create_backup_archive(
    *,
    trigger: str,
    reason: str,
    run_id: int | None = None,
    archive_path: Path | None = None,
) -> tuple[Path, int]:
    backup_root = get_backup_root()
    backup_root.mkdir(parents=True, exist_ok=True)

    id_part = f"_{run_id}" if run_id else ""
    archive_path = archive_path or backup_root / f"doc_helper_backup_{_timestamp()}{id_part}.zip"
    with tempfile.TemporaryDirectory(prefix="doc_helper_backup_") as tmp:
        tmp_dir = Path(tmp)
        db_path = _dump_database(tmp_dir)

        with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(db_path, db_path.name)
            media_files_count = _write_media_files(archive)

            meta_path = tmp_dir / "meta.json"
            _write_meta(
                meta_path,
                trigger=trigger,
                reason=reason,
                run_id=run_id,
                media_files_count=media_files_count,
            )
            archive.write(meta_path, meta_path.name)

    return archive_path, archive_path.stat().st_size


def create_filesystem_backup(
    *,
    trigger: str = BackupRun.Trigger.DEPLOY,
    reason: str = "",
) -> BackupResult:
    trigger = trigger.upper()
    if trigger == BackupRun.Trigger.DEPLOY:
        backup_root = get_backup_root()
        backup_root.mkdir(parents=True, exist_ok=True)
        final_path = backup_root / "doc_helper_deploy_latest.zip"
        tmp_path = backup_root / "doc_helper_deploy_latest.tmp.zip"
        if tmp_path.exists():
            tmp_path.unlink()

        try:
            archive_path, size_bytes = _create_backup_archive(
                trigger=trigger,
                reason=reason,
                archive_path=tmp_path,
            )
            archive_path.replace(final_path)
            return _finish_backup_result(run=None, path=final_path, size_bytes=final_path.stat().st_size)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    archive_path, size_bytes = _create_backup_archive(trigger=trigger, reason=reason)
    return _finish_backup_result(run=None, path=archive_path, size_bytes=size_bytes)


def create_backup(
    *,
    trigger: str = BackupRun.Trigger.MANUAL,
    user=None,
    reason: str = "",
) -> BackupResult:
    run = BackupRun.objects.create(
        created_by=user if getattr(user, "is_authenticated", False) else None,
        trigger=trigger,
        reason=reason,
    )
    return create_backup_for_run(run)


def create_backup_for_run(run: BackupRun) -> BackupResult:
    archive_path = None
    archive_created = False
    try:
        archive_path, size_bytes = _create_backup_archive(trigger=run.trigger, reason=run.reason, run_id=run.pk)
        archive_created = True
        result = _finish_backup_result(run=run, path=archive_path, size_bytes=size_bytes)
        run.mark_success(file_path=str(archive_path), size_bytes=size_bytes, s3_key=result.s3_key)
        return result
    except Exception as exc:
        if archive_path and archive_path.exists() and not archive_created:
            archive_path.unlink()
        run.mark_failed(str(exc))
        raise


def create_backup_for_run_in_background(run_id: int) -> None:
    close_old_connections()
    try:
        run = BackupRun.objects.get(pk=run_id)
        create_backup_for_run(run)
    finally:
        close_old_connections()
