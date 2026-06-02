from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.db import connections
from django.utils import timezone

from backup_app.models import BackupRun


@dataclass(frozen=True)
class BackupResult:
    run: BackupRun | None
    path: Path
    size_bytes: int


def get_backup_root() -> Path:
    root = getattr(settings, "BACKUP_ROOT", None)
    return Path(root) if root else Path(settings.BASE_DIR) / "backups"


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

    command = [
        "pg_dump",
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


def _tar_media(output_path: Path) -> None:
    media_root = Path(settings.MEDIA_ROOT)
    if not media_root.exists():
        return

    backup_root = get_backup_root().resolve()
    with tarfile.open(output_path, "w:gz") as archive:
        for path in media_root.rglob("*"):
            resolved = path.resolve()
            if resolved == backup_root or backup_root in resolved.parents:
                continue
            archive.add(path, arcname=Path("media") / path.relative_to(media_root))


def _write_meta(output_path: Path, *, trigger: str, reason: str, run_id: int | None = None) -> None:
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "trigger": trigger,
        "run_id": run_id,
        "reason": reason,
        "git_sha": _git_sha(),
        "database_engine": connections["default"].settings_dict["ENGINE"],
        "media_root": str(settings.MEDIA_ROOT),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _create_backup_archive(*, trigger: str, reason: str, run_id: int | None = None) -> tuple[Path, int]:
    backup_root = get_backup_root()
    backup_root.mkdir(parents=True, exist_ok=True)

    id_part = f"_{run_id}" if run_id else ""
    archive_path = backup_root / f"doc_helper_backup_{_timestamp()}{id_part}.zip"
    with tempfile.TemporaryDirectory(prefix="doc_helper_backup_") as tmp:
        tmp_dir = Path(tmp)
        db_path = _dump_database(tmp_dir)
        media_path = tmp_dir / "media.tar.gz"
        _tar_media(media_path)
        meta_path = tmp_dir / "meta.json"
        _write_meta(meta_path, trigger=trigger, reason=reason, run_id=run_id)

        with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(db_path, db_path.name)
            if media_path.exists():
                archive.write(media_path, media_path.name)
            archive.write(meta_path, meta_path.name)

    return archive_path, archive_path.stat().st_size


def create_filesystem_backup(
    *,
    trigger: str = BackupRun.Trigger.DEPLOY,
    reason: str = "",
) -> BackupResult:
    archive_path, size_bytes = _create_backup_archive(trigger=trigger, reason=reason)
    return BackupResult(run=None, path=archive_path, size_bytes=size_bytes)


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
    archive_path = None
    try:
        archive_path, size_bytes = _create_backup_archive(trigger=trigger, reason=reason, run_id=run.pk)
        run.mark_success(file_path=str(archive_path), size_bytes=size_bytes)
        return BackupResult(run=run, path=archive_path, size_bytes=size_bytes)
    except Exception as exc:
        if archive_path and archive_path.exists():
            archive_path.unlink()
        run.mark_failed(str(exc))
        raise
