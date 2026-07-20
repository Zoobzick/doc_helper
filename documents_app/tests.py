from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from acts_app.models import Act, AttachmentType
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
from documents_app.services.id_handover.material_registry_projection import (
    build_material_registry_documents,
    get_batch_appendix_sheets_count,
)
from documents_app.views import DocumentBatchRefreshProjectCompositionView
from projects_app.models import Project


class _FakeRelatedManager:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


def _db_material(*, item_id, material_name, document_number, sheets_count):
    passport = SimpleNamespace(
        document_name="Паспорт качества",
        document_number=document_number,
        document_date=date(2026, 2, 11),
        material=SimpleNamespace(name=material_name),
    )
    return SimpleNamespace(
        id=item_id,
        passport_id=item_id,
        passport=passport,
        manual_name="",
        manual_doc_name="",
        manual_doc_no="",
        manual_doc_date=None,
        manual_doc_date_text="",
        sheets_count=sheets_count,
    )


def _manual_material(*, item_id, material_name, sheets_count):
    return SimpleNamespace(
        id=item_id,
        passport_id=None,
        passport=None,
        manual_name=material_name,
        manual_doc_name="Паспорт качества",
        manual_doc_no="123",
        manual_doc_date=date(2026, 2, 11),
        manual_doc_date_text="",
        sheets_count=sheets_count,
    )


class MaterialRegistryProjectionTests(SimpleTestCase):
    def test_db_passports_are_grouped_but_manual_rows_stay_separate(self):
        materials = [
            _db_material(item_id=1, material_name="Материал А", document_number="№123", sheets_count=1),
            _manual_material(item_id=2, material_name="Материал В", sheets_count=1),
            _db_material(item_id=3, material_name="Материал Б", document_number="123", sheets_count=2),
            _manual_material(item_id=4, material_name="Материал Г", sheets_count=1),
        ]

        documents = build_material_registry_documents(materials)

        self.assertEqual(len(documents), 3)
        self.assertEqual(documents[0].material_names, ("Материал А", "Материал Б"))
        self.assertEqual(documents[0].source_item_ids, (1, 3))
        self.assertEqual(documents[0].sheets_count, 2)
        self.assertFalse(documents[0].is_manual)
        self.assertEqual([document.source_item_ids for document in documents[1:]], [(2,), (4,)])
        self.assertTrue(all(document.is_manual for document in documents[1:]))

    def test_different_db_passport_dates_are_not_grouped(self):
        first = _db_material(item_id=1, material_name="Материал А", document_number="123", sheets_count=1)
        second = _db_material(item_id=2, material_name="Материал Б", document_number="123", sheets_count=1)
        second.passport.document_date = date(2026, 2, 12)

        documents = build_material_registry_documents([first, second])

        self.assertEqual(len(documents), 2)

    def test_batch_registry_sheets_count_uses_unique_db_passports_and_all_manual_rows(self):
        materials = [
            _db_material(item_id=1, material_name="Материал А", document_number="123", sheets_count=2),
            _db_material(item_id=2, material_name="Материал Б", document_number="№123", sheets_count=2),
            _manual_material(item_id=3, material_name="Материал В", sheets_count=1),
            _manual_material(item_id=4, material_name="Материал Г", sheets_count=1),
        ]
        appendix_line = SimpleNamespace(
            sheets_count=99,
            source_attachment=SimpleNamespace(type=AttachmentType.MATERIALS_REGISTRY, sheets_count=1),
            act=SimpleNamespace(materials=_FakeRelatedManager(materials)),
        )

        sheets_count = get_batch_appendix_sheets_count(appendix_line)

        self.assertEqual(sheets_count, 5)


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
