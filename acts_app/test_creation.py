from contextlib import ExitStack
from importlib import import_module
from types import SimpleNamespace
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from acts_app.forms import ActForm
from acts_app.models import Act, ActStatus
from projects_app.models import Project


class ActCreationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="creator", password="test")
        self.client.force_login(self.user)
        self.project = Project.objects.create(full_code="creation-test")
        self.url = reverse("acts_app:act_create")
        self.data = {
            "projects": str(self.project.pk), "number": "1", "act_date": "2026-10-21",
            "work_name": "Работы", "work_start_date": "2026-10-20", "work_end_date": "2026-10-21",
            "copies_count": "3", "mat-TOTAL_FORMS": "0", "mat-INITIAL_FORMS": "0",
            "att-TOTAL_FORMS": "1", "att-INITIAL_FORMS": "0", "att-0-title": "Исполнительная схема",
            "att-0-doc_no": "1", "att-0-doc_date": "2026-10-21", "att-0-sheets_count": "1",
            "att-0-original_state": "2", "recovery_token": "test-recovery-token",
        }
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("AppendixBuilder", "generate_act_docx", "generate_and_save_registry_p3_docx"):
            self.stack.enter_context(patch("acts_app.views." + name))

    def test_new_form_is_not_draft(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"]["is_draft"].value())

    def test_create_defaults_to_ready_and_acknowledges_recovery(self):
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 302)
        act = Act.objects.get()
        self.assertEqual(act.status, ActStatus.READY)
        self.assertEqual(act.attachments.count(), 1)
        self.assertEqual(list(act.projects.all()), [self.project])
        self.assertIn("?saved_form=test-recovery-token", response.url)

    def test_explicit_draft_can_be_completed(self):
        response = self.client.post(self.url, {**self.data, "is_draft": "on"})
        self.assertEqual(response.status_code, 302)
        act = Act.objects.get()
        self.assertEqual(act.status, ActStatus.DRAFT)
        form = ActForm(self.data, instance=act)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().status, ActStatus.READY)

    def test_validation_failure_keeps_data_and_does_not_acknowledge_recovery(self):
        response = self.client.post(self.url, {**self.data, "number": "", "is_draft": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Act.objects.exists())
        self.assertTrue(response.context["form"]["is_draft"].value())
        self.assertEqual(response.context["form"]["work_name"].value(), "Работы")
        self.assertContains(response, 'value="test-recovery-token"')

    def test_list_filters_only_drafts_and_combines_search(self):
        for number, status in [("draft-1", ActStatus.DRAFT), ("draft-2", ActStatus.DRAFT), ("ready", ActStatus.READY), ("final", ActStatus.FINAL)]:
            Act.objects.create(number=number, status=status, act_date=date(2026, 10, 21), work_name="Работы")
        response = self.client.get(reverse("acts_app:act_list"), {"status": "DRAFT", "q": "draft-1"})
        self.assertEqual([act.number for act in response.context["acts"]], ["draft-1"])
        self.assertContains(response, 'id="act-drafts-filter" name="status" value="DRAFT"')
        self.assertNotContains(response, 'id="act-status-filter"')
        self.assertContains(response, 'class="act-draft-row"', count=1)
        self.assertNotContains(response, '>Черновик</span>')
        response = self.client.get(reverse("acts_app:act_list"))
        self.assertEqual(len(response.context["acts"]), 4)
        self.assertContains(response, 'class="act-draft-row"', count=2)
        for old_status in ("READY", "FINAL"):
            response = self.client.get(reverse("acts_app:act_list"), {"status": old_status})
            self.assertEqual(len(response.context["acts"]), 4)

    def test_existing_drafts_are_migrated_without_changing_final_or_future_drafts(self):
        old = Act.objects.create(number="old", status=ActStatus.DRAFT, act_date=date(2026, 10, 21))
        final = Act.objects.create(number="final", status=ActStatus.FINAL, act_date=date(2026, 10, 21))
        migration = import_module("acts_app.migrations.0024_existing_acts_ready")
        migration.mark_existing_acts_ready(apps, SimpleNamespace(connection=connection))
        old.refresh_from_db()
        final.refresh_from_db()
        self.assertEqual(old.status, ActStatus.READY)
        self.assertEqual(final.status, ActStatus.FINAL)
        new = Act.objects.create(number="new", status=ActStatus.DRAFT, act_date=date(2026, 10, 21))
        self.assertEqual(new.status, ActStatus.DRAFT)

    def test_edit_preserves_final_status(self):
        act = Act.objects.create(number="final", status=ActStatus.FINAL, act_date=date(2026, 10, 21))
        form = ActForm(self.data, instance=act)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().status, ActStatus.FINAL)
