import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase
from django.test.utils import override_settings

from backup_app.services import _create_backup_archive


class BackupMediaFilesTests(SimpleTestCase):
    @patch("backup_app.services._git_sha", return_value="test-sha")
    @patch("backup_app.services._dump_database")
    def test_registry_xlsx_and_pdf_are_included_in_backup(self, dump_database, _git_sha):
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            media_root = tmp_path / "media"
            backup_root = tmp_path / "backups"
            registry_dir = media_root / "documents" / "batches" / "registry"
            registry_dir.mkdir(parents=True)

            xlsx_path = registry_dir / "registry.xlsx"
            pdf_path = registry_dir / "registry.pdf"
            xlsx_path.write_bytes(b"xlsx")
            pdf_path.write_bytes(b"pdf")

            def create_database_dump(output_dir):
                database_path = output_dir / "db.dump"
                database_path.write_bytes(b"database")
                return database_path

            dump_database.side_effect = create_database_dump

            with override_settings(MEDIA_ROOT=media_root, BACKUP_ROOT=backup_root):
                archive_path, _ = _create_backup_archive(
                    trigger="manual",
                    reason="test",
                )

            with zipfile.ZipFile(archive_path) as archive:
                archive_names = set(archive.namelist())

            self.assertIn("media/documents/batches/registry/registry.xlsx", archive_names)
            self.assertIn("media/documents/batches/registry/registry.pdf", archive_names)
