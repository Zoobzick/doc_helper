from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from acts_app.models import Act
from documents_app.models import (
    DocumentBatch,
    DocumentBatchAct,
    DocumentBatchActSource,
    DocumentBatchGenerationMode,
    DocumentBatchProject,
    DocumentBatchProjectReviewStatus,
    DocumentBatchProjectScope,
    DocumentBatchSelectionMode,
)
from documents_app.views import DocumentBatchRefreshProjectCompositionView
from projects_app.models import Project


class DocumentBatchRefreshProjectCompositionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="batch-editor")
        self.project = Project.objects.create(full_code="CODE-1")
        self.other_project = Project.objects.create(full_code="CODE-2")
        self.batch = DocumentBatch.objects.create(
            created_by=self.user,
            selection_mode=DocumentBatchSelectionMode.RANGE,
            month_from="01.2026",
            month_to="01.2026",
            generation_mode=DocumentBatchGenerationMode.REGISTRY_ONLY,
            project_scope=DocumentBatchProjectScope.MULTI_PROJECT,
        )
        self.batch_project = DocumentBatchProject.objects.create(
            batch=self.batch,
            project=self.project,
            order=1,
            review_status=DocumentBatchProjectReviewStatus.REVIEWED,
            reviewed_by=self.user,
        )
        self.other_batch_project = DocumentBatchProject.objects.create(
            batch=self.batch,
            project=self.other_project,
            order=2,
            review_status=DocumentBatchProjectReviewStatus.REVIEWED,
            reviewed_by=self.user,
        )

    def _create_act(self, *, number, project, act_date):
        act = Act.objects.create(
            created_by=self.user,
            number=number,
            act_date=act_date,
            work_name=f"Работа {number}",
        )
        act.projects.add(project)
        return act

    @patch("documents_app.views.DocumentBatchPreviewBuilder.build_and_save_snapshot")
    def test_refresh_updates_only_requested_project(self, build_snapshot):
        old_act = self._create_act(
            number="1",
            project=self.project,
            act_date=date(2026, 1, 10),
        )
        new_act = self._create_act(
            number="2",
            project=self.project,
            act_date=date(2026, 1, 20),
        )
        manual_act = self._create_act(
            number="3",
            project=self.project,
            act_date=date(2026, 2, 1),
        )
        other_act = self._create_act(
            number="4",
            project=self.other_project,
            act_date=date(2026, 1, 15),
        )
        DocumentBatchAct.objects.create(
            batch=self.batch,
            project=self.project,
            act=old_act,
            order=1,
            source=DocumentBatchActSource.AUTO,
            added_by=self.user,
        )
        DocumentBatchAct.objects.create(
            batch=self.batch,
            project=self.project,
            act=manual_act,
            order=2,
            source=DocumentBatchActSource.MANUAL,
            added_by=self.user,
        )
        other_batch_act = DocumentBatchAct.objects.create(
            batch=self.batch,
            project=self.other_project,
            act=other_act,
            order=1,
            source=DocumentBatchActSource.AUTO,
            added_by=self.user,
        )

        result = DocumentBatchRefreshProjectCompositionView()._refresh_project_composition(
            batch=self.batch,
            batch_project=self.batch_project,
        )

        refreshed_items = list(
            DocumentBatchAct.objects.filter(batch=self.batch, project=self.project)
            .order_by("order")
            .values_list("act_id", "source")
        )
        self.assertEqual(
            refreshed_items,
            [
                (old_act.id, DocumentBatchActSource.AUTO),
                (new_act.id, DocumentBatchActSource.AUTO),
                (manual_act.id, DocumentBatchActSource.MANUAL),
            ],
        )
        self.assertTrue(
            DocumentBatchAct.objects.filter(pk=other_batch_act.pk).exists()
        )
        self.other_batch_project.refresh_from_db()
        self.assertEqual(
            self.other_batch_project.review_status,
            DocumentBatchProjectReviewStatus.REVIEWED,
        )
        self.batch_project.refresh_from_db()
        self.assertEqual(
            self.batch_project.review_status,
            DocumentBatchProjectReviewStatus.PENDING,
        )
        self.assertEqual(result["auto_acts_count"], 2)
        self.assertEqual(result["manual_acts_count"], 1)
        build_snapshot.assert_called_once_with(batch=self.batch)
