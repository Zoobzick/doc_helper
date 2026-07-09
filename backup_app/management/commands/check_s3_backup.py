from __future__ import annotations

import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from backup_app.services import delete_backup_from_s3, is_s3_backup_enabled, upload_backup_to_s3


class Command(BaseCommand):
    help = "Upload a small temporary file to configured S3 backup storage."

    def handle(self, *args, **options):
        if not is_s3_backup_enabled():
            raise CommandError("S3 backup is disabled. Set S3_BACKUP_ENABLED=true first.")

        with tempfile.NamedTemporaryFile(prefix="doc_helper_s3_check_", suffix=".txt", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(b"doc_helper S3 backup check\n")

        s3_key = ""
        try:
            s3_key = upload_backup_to_s3(tmp_path)
            delete_backup_from_s3(s3_key)
        except Exception as exc:
            raise CommandError(f"S3 upload failed: {exc}") from exc
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

        self.stdout.write(self.style.SUCCESS(f"S3 upload/delete OK: {s3_key}"))
