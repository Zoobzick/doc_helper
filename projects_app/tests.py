import io
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from projects_app.models import Project, ProjectRevision, TempUpload
from projects_app.services import (
    assign_full_code_to_draft,
    attach_revision_to_project,
    change_project_full_code,
    delete_project_revision,
    process_single_pdf,
    safe_zip_relpath,
    set_revision_in_production,
)


PDF_BYTES = b"%PDF-1.4\n% doc-helper test file\n"


class ProjectsAppTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_superuser(
            email="admin@example.com",
            username="admin",
            password="password123",
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.projects_tmpdir = tempfile.TemporaryDirectory(prefix="doc_helper_projects_")
        self.override = override_settings(PROJECTS_DIR=self.projects_tmpdir.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.projects_tmpdir.cleanup)
        self.addCleanup(self._cleanup_temp_upload_files)

    def _cleanup_temp_upload_files(self):
        for upload in TempUpload.objects.all():
            try:
                Path(upload.tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    def _make_source_pdf(self, *, stem: str, content: bytes = PDF_BYTES) -> Path:
        source_dir = Path(tempfile.mkdtemp(prefix="doc_helper_source_pdf_"))
        self.addCleanup(shutil.rmtree, source_dir, True)
        pdf_path = source_dir / f"{stem}.pdf"
        pdf_path.write_bytes(content)
        return pdf_path

    def _attach_revision(self, *, project: Project, stem: str, content: bytes) -> ProjectRevision:
        pdf_path = self._make_source_pdf(stem=stem, content=content)
        revision, _ = attach_revision_to_project(
            project=project,
            file_name=pdf_path.name,
            temp_file_path=str(pdf_path),
            sha256=None,
        )
        return revision

    def test_process_single_pdf_creates_project_and_latest_revision(self):
        pdf_path = self._make_source_pdf(stem="TEST-PROJECT-001")

        result = process_single_pdf(
            pdf_path=pdf_path,
            original_name=pdf_path.name,
            user=self.user,
        )

        self.assertEqual(result["status"], "created")

        project = Project.objects.get(full_code="TEST-PROJECT-001")
        revision = ProjectRevision.objects.get(project=project)

        self.assertEqual(revision.revision, "01")
        self.assertTrue(revision.is_latest)
        self.assertTrue(Path(revision.file_path).exists())
        self.assertEqual(Path(revision.file_path).parent, Path(self.projects_tmpdir.name).resolve())

    def test_process_single_pdf_marks_duplicate_by_sha256(self):
        first_pdf = self._make_source_pdf(stem="DUPLICATE-001", content=PDF_BYTES)
        second_pdf = self._make_source_pdf(stem="DUPLICATE-002", content=PDF_BYTES)

        first_result = process_single_pdf(
            pdf_path=first_pdf,
            original_name=first_pdf.name,
            user=self.user,
        )
        second_result = process_single_pdf(
            pdf_path=second_pdf,
            original_name=second_pdf.name,
            user=self.user,
        )

        self.assertEqual(first_result["status"], "created")
        self.assertEqual(second_result, {"status": "duplicate", "file": second_pdf.name})
        self.assertEqual(ProjectRevision.objects.count(), 1)
        self.assertEqual(Project.objects.count(), 1)

    def test_upload_temp_and_create_project_from_pdf(self):
        upload_response = self.client.post(
            reverse("projects:project_upload_temp"),
            {
                "file": SimpleUploadedFile(
                    "uploaded-project.pdf",
                    PDF_BYTES,
                    content_type="application/pdf",
                )
            },
        )

        self.assertEqual(upload_response.status_code, 200)
        payload = upload_response.json()
        upload_id = payload["upload_id"]

        save_response = self.client.post(
            reverse("projects:project_create_save"),
            {
                "upload_id": upload_id,
                "full_code": "FORM-PROJECT-001",
                "construction": "Test construction",
            },
        )

        self.assertRedirects(
            save_response,
            reverse("projects:projects_list"),
            fetch_redirect_response=False,
        )

        project = Project.objects.get(full_code="FORM-PROJECT-001")
        revision = ProjectRevision.objects.get(project=project)
        temp_upload = TempUpload.objects.get(id=upload_id)

        self.assertTrue(temp_upload.is_used)
        self.assertTrue(Path(revision.file_path).exists())
        self.assertEqual(revision.file_name, "uploaded-project.pdf")

    def test_project_upload_archive_imports_nested_pdf_files(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("nested/ARCHIVE-PROJECT-001.pdf", PDF_BYTES)
            archive.writestr("nested/readme.txt", b"skip me")

        archive_response = self.client.post(
            reverse("projects:project_upload_archive"),
            {
                "archive": SimpleUploadedFile(
                    "projects.zip",
                    archive_bytes.getvalue(),
                    content_type="application/zip",
                )
            },
        )

        self.assertEqual(archive_response.status_code, 200)
        payload = archive_response.json()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["processed"], 1)
        self.assertEqual(payload["results"][0]["status"], "created")

        project = Project.objects.get(full_code="ARCHIVE-PROJECT-001")
        revision = ProjectRevision.objects.get(project=project)
        self.assertTrue(Path(revision.file_path).exists())

    def test_safe_zip_relpath_blocks_zip_slip_patterns(self):
        self.assertEqual(safe_zip_relpath("nested/project.pdf"), "nested/project.pdf")
        self.assertIsNone(safe_zip_relpath("../escape.pdf"))
        self.assertIsNone(safe_zip_relpath("nested/../../escape.pdf"))
        self.assertIsNone(safe_zip_relpath("/absolute.pdf"))
        self.assertIsNone(safe_zip_relpath("C:/absolute.pdf"))
        self.assertIsNone(safe_zip_relpath("folder/"))

    def test_project_upload_archive_skips_dangerous_paths(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("../../ESCAPE-PROJECT.pdf", PDF_BYTES)
            archive.writestr("safe/SAFE-PROJECT.pdf", PDF_BYTES)

        archive_response = self.client.post(
            reverse("projects:project_upload_archive"),
            {
                "archive": SimpleUploadedFile(
                    "dangerous.zip",
                    archive_bytes.getvalue(),
                    content_type="application/zip",
                )
            },
        )

        self.assertEqual(archive_response.status_code, 200)
        payload = archive_response.json()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["processed"], 1)
        self.assertEqual(payload["results"][0]["project"], "SAFE-PROJECT")
        self.assertFalse(Project.objects.filter(full_code="ESCAPE-PROJECT").exists())

    def test_change_project_full_code_merges_revisions_into_existing_project(self):
        target = Project.objects.create(full_code="MERGE-TARGET", construction="Target", needs_review=True)
        source = Project.objects.create(full_code="MERGE-SOURCE", construction="Source", needs_review=True)

        self._attach_revision(project=target, stem="merge-target-a", content=b"%PDF-1.4\n% target\n")
        self._attach_revision(project=source, stem="merge-source-b", content=b"%PDF-1.4\n% source\n")

        merged = change_project_full_code(source, "MERGE-TARGET")

        self.assertEqual(merged.pk, target.pk)
        self.assertFalse(Project.objects.filter(pk=source.pk).exists())

        revisions = list(ProjectRevision.objects.filter(project=target).order_by("created_at", "id"))
        self.assertEqual([rev.revision for rev in revisions], ["01", "02"])
        self.assertEqual(sum(1 for rev in revisions if rev.is_latest), 1)
        self.assertEqual(revisions[-1].revision, "02")
        self.assertTrue(revisions[-1].is_latest)
        self.assertTrue(all(Path(rev.file_path).exists() for rev in revisions))
        self.assertTrue(all("MERGE-TARGET" in Path(rev.file_path).name for rev in revisions))

    def test_assign_full_code_to_draft_merges_draft_into_existing_project(self):
        existing = Project.objects.create(full_code="FINAL-CODE", construction="Existing", needs_review=True)
        draft = Project.objects.create(full_code=None, construction="Draft", needs_review=True)

        self._attach_revision(project=existing, stem="existing-rev", content=b"%PDF-1.4\n% existing\n")
        self._attach_revision(project=draft, stem="draft-rev", content=b"%PDF-1.4\n% draft\n")

        merged = assign_full_code_to_draft(draft_project_id=draft.id, full_code_input="FINAL-CODE")

        self.assertEqual(merged.pk, existing.pk)
        self.assertFalse(Project.objects.filter(pk=draft.pk).exists())

        revisions = list(ProjectRevision.objects.filter(project=existing).order_by("created_at", "id"))
        self.assertEqual([rev.revision for rev in revisions], ["01", "02"])
        self.assertEqual(sum(1 for rev in revisions if rev.is_latest), 1)
        self.assertTrue(revisions[-1].is_latest)
        self.assertTrue(all(Path(rev.file_path).exists() for rev in revisions))
        self.assertTrue(all("FINAL-CODE" in Path(rev.file_path).name for rev in revisions))

    def test_set_revision_in_production_updates_flag(self):
        project = Project.objects.create(full_code="PRODUCTION-FLAG", construction="Test", needs_review=True)
        revision = self._attach_revision(
            project=project,
            stem="production-flag",
            content=b"%PDF-1.4\n% production flag\n",
        )

        updated = set_revision_in_production(revision_id=revision.id, value=True)
        self.assertTrue(updated.in_production)

        revision.refresh_from_db()
        self.assertTrue(revision.in_production)

        updated = set_revision_in_production(revision_id=revision.id, value=False)
        self.assertFalse(updated.in_production)

        revision.refresh_from_db()
        self.assertFalse(revision.in_production)

    def test_delete_project_revision_removes_file_and_promotes_latest(self):
        project = Project.objects.create(full_code="DELETE-WITH-REMAINDER", construction="Test", needs_review=True)
        first_revision = self._attach_revision(
            project=project,
            stem="delete-remainder-1",
            content=b"%PDF-1.4\n% first revision\n",
        )
        second_revision = self._attach_revision(
            project=project,
            stem="delete-remainder-2",
            content=b"%PDF-1.4\n% second revision\n",
        )

        first_path = Path(first_revision.file_path)
        second_path = Path(second_revision.file_path)

        self.assertTrue(first_path.exists())
        self.assertTrue(second_path.exists())
        self.assertTrue(second_revision.is_latest)

        delete_project_revision(revision_id=second_revision.id)

        self.assertFalse(ProjectRevision.objects.filter(id=second_revision.id).exists())
        self.assertFalse(second_path.exists())
        self.assertTrue(Project.objects.filter(id=project.id).exists())

        first_revision.refresh_from_db()
        self.assertTrue(first_revision.is_latest)
        self.assertTrue(first_path.exists())

    def test_delete_project_revision_deletes_project_when_last_revision_removed(self):
        project = Project.objects.create(full_code="DELETE-LAST", construction="Test", needs_review=True)
        revision = self._attach_revision(
            project=project,
            stem="delete-last-only",
            content=b"%PDF-1.4\n% last revision\n",
        )

        revision_path = Path(revision.file_path)
        self.assertTrue(revision_path.exists())

        delete_project_revision(revision_id=revision.id)

        self.assertFalse(ProjectRevision.objects.filter(id=revision.id).exists())
        self.assertFalse(Project.objects.filter(id=project.id).exists())
        self.assertFalse(revision_path.exists())
