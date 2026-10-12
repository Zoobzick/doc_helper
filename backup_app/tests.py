import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase
from django.test.utils import override_settings

from backup_app.services import _create_backup_archive, create_filesystem_backup, get_s3_marker_path


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


@override_settings(S3_BACKUP_ENABLED=True, S3_BACKUP_BUCKET="test-bucket", S3_BACKUP_PREFIX="backups")
class ScheduledBackupTests(SimpleTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        settings_override = override_settings(BACKUP_ROOT=self.root, MEDIA_ROOT=self.root / "media")
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.final = self.root / "doc_helper_scheduled_latest.zip"
        self.final.write_bytes(b"previous backup")
        self.marker = get_s3_marker_path(self.final)
        self.marker.write_text("previous marker", encoding="utf-8")
        self.client_patch = patch("backup_app.services._get_s3_backup_client")
        self.client = self.client_patch.start().return_value
        self.addCleanup(self.client_patch.stop)

    def _archive(self, *, archive_path, **kwargs):
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("db.dump", "new database")
            archive.writestr("media/document.pdf", "document")
            archive.writestr("meta.json", "{}")
        return archive_path, archive_path.stat().st_size

    @patch("backup_app.services._create_backup_archive")
    def test_replaces_same_local_file_and_s3_key_only_after_upload(self, create_archive):
        create_archive.side_effect = self._archive
        manual = self.root / "doc_helper_backup_manual.zip"
        manual.write_bytes(b"manual")
        deploy = self.root / "doc_helper_deploy_latest.zip"
        deploy.write_bytes(b"deploy")
        def upload(filename, bucket, key):
            self.assertEqual(self.final.read_bytes(), b"previous backup")
            self.assertEqual(bucket, "test-bucket")
            self.assertEqual(key, "backups/doc_helper_scheduled_latest.zip")
            with zipfile.ZipFile(filename) as archive:
                self.assertIn("db.dump", archive.namelist())
        self.client.upload_file.side_effect = upload
        first = create_filesystem_backup(trigger="scheduled")
        self.client.upload_file.side_effect = None
        second = create_filesystem_backup(trigger="scheduled")
        self.assertEqual(first.path, second.path)
        self.assertEqual(first.s3_key, second.s3_key)
        self.assertEqual(self.client.upload_file.call_count, 2)
        self.assertTrue(zipfile.is_zipfile(self.final))
        self.assertEqual(manual.read_bytes(), b"manual")
        self.assertEqual(deploy.read_bytes(), b"deploy")
        self.assertFalse(list(self.root.glob("scheduled_*")))

    @patch("backup_app.services._create_backup_archive")
    def test_upload_failure_preserves_previous_backup_and_marker(self, create_archive):
        create_archive.side_effect = self._archive
        self.client.upload_file.side_effect = RuntimeError("S3 unavailable")
        with self.assertRaisesRegex(RuntimeError, "S3 unavailable"):
            create_filesystem_backup(trigger="scheduled")
        self.assertEqual(self.final.read_bytes(), b"previous backup")
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "previous marker")
        self.assertFalse(list(self.root.glob("scheduled_*")))

    @patch("backup_app.services._create_backup_archive")
    def test_archive_failure_preserves_previous_backup(self, create_archive):
        def fail(*, archive_path, **kwargs):
            archive_path.write_bytes(b"partial")
            raise RuntimeError("dump failed")
        create_archive.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, "dump failed"):
            create_filesystem_backup(trigger="scheduled")
        self.assertEqual(self.final.read_bytes(), b"previous backup")
        self.client.upload_file.assert_not_called()
        self.assertFalse(list(self.root.glob("scheduled_*")))

    @override_settings(S3_BACKUP_ENABLED=False)
    def test_disabled_s3_fails_without_replacing_backup(self):
        with self.assertRaisesRegex(RuntimeError, "S3_BACKUP_ENABLED"):
            create_filesystem_backup(trigger="scheduled")
        self.assertEqual(self.final.read_bytes(), b"previous backup")

    @patch("backup_app.management.commands.create_backup.create_filesystem_backup")
    def test_scheduled_command_uses_single_filesystem_backup(self, create_backup):
        from django.core.management import call_command
        from backup_app.services import BackupResult
        create_backup.return_value = BackupResult(run=None, path=self.final, size_bytes=10)
        call_command("create_backup", trigger="scheduled", verbosity=0)
        create_backup.assert_called_once_with(trigger="SCHEDULED", reason="")
