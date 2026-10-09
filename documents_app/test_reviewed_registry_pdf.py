from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from acts_app.models import Act
from documents_app.models import (
    DocumentBatch, DocumentBatchAct, DocumentBatchProject,
    DocumentBatchProjectReviewStatus, GeneratedDocumentType,
)
from projects_app.models import Project


class ReviewedRegistryPdfTests(TestCase):
    def setUp(self):
        storage = TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        settings = override_settings(
            MEDIA_ROOT=storage.name,
            DOCUMENTS_DIR=Path(storage.name) / "documents",
        )
        settings.enable()
        self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_superuser(username="reviewer", password="test")
        self.client.force_login(self.user)
        self.batch = DocumentBatch.objects.create(created_by=self.user)
        self.project = Project.objects.create(full_code="REVIEW-1")
        self.other = Project.objects.create(full_code="REVIEW-2")
        self.batch_project = DocumentBatchProject.objects.create(batch=self.batch, project=self.project, order=1)
        DocumentBatchProject.objects.create(batch=self.batch, project=self.other, order=2)
        self.act = Act.objects.create(number="1", act_date=date(2026, 7, 1), work_name="Работы")
        DocumentBatchAct.objects.create(batch=self.batch, project=self.project, act=self.act, order=1, added_by=self.user)
        self.mark_url = reverse("documents:id_handover_batch_project_mark_reviewed", args=[self.batch.pk, self.project.pk])
        self.pdf_url = reverse("documents:id_handover_batch_project_registry_pdf", args=[self.batch.pk, self.project.pk])
        self.master_url = reverse("documents:id_handover_batch_master", args=[self.batch.pk])
        renderer_patch = patch(
            "documents_app.services.id_handover.project_registry_generation_service.ProjectRegistryGenerationService._render_context_to_files",
            return_value=(b"xlsx", b"%PDF-test", 1),
        )
        self.renderer = renderer_patch.start()
        self.addCleanup(renderer_patch.stop)

    def mark_reviewed(self):
        response = self.client.post(self.mark_url)
        self.assertEqual(response.status_code, 302)
        self.batch_project.refresh_from_db()
        return response

    def test_mark_generates_only_reviewed_project_and_exposes_inline_pdf(self):
        self.mark_reviewed()
        self.assertEqual(self.batch_project.review_status, DocumentBatchProjectReviewStatus.REVIEWED)
        self.assertEqual(self.batch_project.reviewed_by, self.user)
        self.assertEqual(set(self.batch.generated_documents.values_list("project_id", flat=True)), {self.project.pk})
        self.assertEqual(self.batch.generated_documents.count(), 2)
        self.assertEqual(self.renderer.call_args.kwargs["context"]["summary"]["acts_count"], 1)
        self.assertEqual(self.renderer.call_args.kwargs["project_id"], self.project.pk)
        response = self.client.get(self.pdf_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response["Content-Disposition"].startswith("inline;"))
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-test")
        response.close()
        response = self.client.get(self.master_url)
        self.assertContains(response, self.pdf_url)
        self.assertContains(response, 'target="_blank" rel="noopener"')

    def test_generation_failure_does_not_mark_reviewed_or_offer_pdf(self):
        self.renderer.side_effect = RuntimeError("Conversion failed")
        self.mark_reviewed()
        self.assertEqual(self.batch_project.review_status, DocumentBatchProjectReviewStatus.PENDING)
        self.assertIsNone(self.batch_project.reviewed_at)
        self.assertFalse(self.batch.generated_documents.exists())
        self.assertNotContains(self.client.get(self.master_url), self.pdf_url)

    def test_one_reviewed_project_out_of_eighteen_keeps_batch_in_review(self):
        for number in range(3, 19):
            project = Project.objects.create(full_code=f"REVIEW-{number}")
            DocumentBatchProject.objects.create(batch=self.batch, project=project, order=number)
        self.mark_reviewed()
        url = reverse("documents:id_handover_batch_list")
        response = self.client.get(url)
        batch = response.context["batches"][0]
        self.assertEqual(batch.projects_count, 18)
        self.assertEqual(batch.reviewed_projects_count, 1)
        self.assertEqual(batch.list_state_key, "in_progress")
        self.assertContains(response, "Продолжить проверку")
        self.assertNotContains(response, "Открыть файлы")
        self.assertEqual(response.context["summary"]["generated"], 0)
        self.assertEqual(len(self.client.get(url, {"status": "in_progress"}).context["batches"]), 1)
        self.assertEqual(len(self.client.get(url, {"status": "generated"}).context["batches"]), 0)
        self.assertContains(self.client.get(self.master_url), self.pdf_url)

    def test_all_reviewed_without_files_is_ready_to_generate(self):
        self.batch.batch_projects.update(review_status=DocumentBatchProjectReviewStatus.REVIEWED)
        response = self.client.get(reverse("documents:id_handover_batch_list"))
        self.assertEqual(response.context["batches"][0].list_state_key, "ready_to_generate")
        self.assertContains(response, "Открыть карточку")

    def test_completed_batch_returns_to_review_after_reset(self):
        self.mark_reviewed()
        DocumentBatchAct.objects.create(batch=self.batch, project=self.other, act=self.act, order=1, added_by=self.user)
        self.client.post(reverse("documents:id_handover_batch_project_mark_reviewed", args=[self.batch.pk, self.other.pk]))
        url = reverse("documents:id_handover_batch_list")
        response = self.client.get(url)
        self.assertEqual(response.context["batches"][0].list_state_key, "generated")
        self.assertContains(response, "Открыть файлы")
        self.batch.batch_projects.filter(project=self.other).update(review_status=DocumentBatchProjectReviewStatus.PENDING)
        response = self.client.get(url)
        self.assertEqual(response.context["batches"][0].list_state_key, "in_progress")
        self.assertContains(response, "Продолжить проверку")
        self.assertNotContains(response, "Открыть файлы")

    def test_unreviewed_batch_with_old_files_still_needs_review(self):
        self.mark_reviewed()
        self.batch.batch_projects.update(review_status=DocumentBatchProjectReviewStatus.PENDING)
        response = self.client.get(reverse("documents:id_handover_batch_list"))
        self.assertEqual(response.context["batches"][0].list_state_key, "needs_review")
        self.assertContains(response, "Начать проверку")
        self.assertNotContains(response, "Открыть файлы")

    def test_changed_act_cannot_open_old_pdf_even_without_invalidating_flag(self):
        self.mark_reviewed()
        Act.objects.filter(pk=self.act.pk).update(work_name="Изменённые работы")
        response = self.client.get(self.pdf_url)
        self.assertEqual(response.status_code, 409)

    def test_reset_review_hides_button_and_blocks_open(self):
        self.mark_reviewed()
        self.batch_project.review_status = DocumentBatchProjectReviewStatus.IN_PROGRESS
        self.batch_project.save(update_fields=["review_status"])
        self.assertEqual(self.client.get(self.pdf_url).status_code, 409)
        self.assertNotContains(self.client.get(self.master_url), self.pdf_url)

    def test_missing_file_returns_404(self):
        self.mark_reviewed()
        document = self.batch.generated_documents.get(document_type=GeneratedDocumentType.REGISTRY_PREVIEW_PDF)
        document.file.storage.delete(document.file.name)
        self.assertEqual(self.client.get(self.pdf_url).status_code, 404)

    def test_repeat_review_regenerates_current_registry(self):
        self.mark_reviewed()
        self.act.work_name = "Новые работы"
        self.act.save()
        self.mark_reviewed()
        self.assertEqual(self.renderer.call_count, 2)
        self.assertEqual(self.batch.generated_documents.count(), 2)
        response = self.client.get(self.pdf_url)
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_permissions_are_required(self):
        user = get_user_model().objects.create_user(username="no-access", email="no-access@example.com")
        self.client.force_login(user)
        self.assertEqual(self.client.post(self.mark_url).status_code, 403)
        self.assertEqual(self.client.get(self.pdf_url).status_code, 403)
        self.renderer.assert_not_called()

    def test_project_from_another_batch_is_not_accessible(self):
        another_batch = DocumentBatch.objects.create(created_by=self.user)
        url = reverse("documents:id_handover_batch_project_registry_pdf", args=[another_batch.pk, self.project.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
