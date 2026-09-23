from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from acts_app.forms import ActMaterialItemForm
from acts_app.models import Act
from acts_app.services.material_resolver import resolve_material_fields
from passports_app.forms import PassportUpdateForm, PassportUploadForm
from passports_app.models import Material, Passport
from passports_app.services import import_single_passport_file


class PassportDocumentDateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="dates", password="test")
        self.passport = Passport.objects.create(
            uploaded_by=self.user, material=Material.objects.create(name="Material"),
            document_name="Passport", document_number="1", document_date=date(2026, 7, 1),
            document_date_text="Июль 2026 года", file="existing.pdf",
        )

    def material_form(self, **overrides):
        values = {"passport": self.passport.pk, "sheets_count": 1}
        values.update(overrides)
        form = ActMaterialItemForm(data=values)
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def test_snapshot_uses_text_and_survives_passport_edit(self):
        item = self.material_form().save(commit=False)
        item.act = Act.objects.create(number="1", act_date=date(2026, 8, 1))
        item.save()
        self.passport.document_date_text = "Август 2026 года"
        self.passport.save()
        item.refresh_from_db()
        self.assertEqual(resolve_material_fields(item)["document_date_str"], "Июль 2026 года")
        new_item = self.material_form().save(commit=False)
        self.assertEqual(resolve_material_fields(new_item)["document_date_str"], "Август 2026 года")

    def test_local_override_does_not_change_passport(self):
        item = self.material_form(manual_doc_date_text="07.2026г.").save(commit=False)
        self.assertEqual(resolve_material_fields(item)["document_date_str"], "07.2026г.")
        self.passport.refresh_from_db()
        self.assertEqual(self.passport.document_date_text, "Июль 2026 года")

    def test_full_date_fallback(self):
        self.passport.document_date_text = ""
        self.passport.save()
        item = self.material_form().save(commit=False)
        self.assertEqual(item.manual_doc_date, date(2026, 7, 1))
        self.assertIn("01.07.2026", resolve_material_fields(item)["document_date_str"])

    def test_update_with_text_only_does_not_require_review(self):
        form = PassportUpdateForm(instance=self.passport, data={
            "material_name": "Material", "document_name": "Passport", "document_number": "1",
            "document_date_text": "07.2026г.", "sheets_count": 1,
        })
        self.assertTrue(form.is_valid(), form.errors)
        passport = form.save()
        self.assertIsNone(passport.document_date)
        self.assertFalse(passport.needs_review)

    def test_upload_accepts_text_without_full_date(self):
        form = PassportUploadForm(data={"document_date_text": "07.2026г."}, files={
            "file": SimpleUploadedFile("passport.psd", b"test"),
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["document_date"])
        # Storage is unrelated to the date contract: avoid writing a passport file.
        with patch("django.db.models.fields.files.FieldFile.save"):
            passport = import_single_passport_file(
                uploaded_file=form.cleaned_data["file"], user=self.user,
                material_name="Material", document_name="Passport", document_number="1",
                document_date_text=form.cleaned_data["document_date_text"],
            )
        self.assertIsNone(passport.document_date)
        self.assertEqual(passport.document_date_display, "07.2026г.")
        self.assertFalse(passport.needs_review)

    def test_picker_returns_both_dates_and_displays_text_first(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("acts_app:passports_datatable"))
        self.assertEqual(response.status_code, 200)
        row = response.json()["data"][0]
        self.assertEqual(row["doc_date"], "Июль 2026 года")
        self.assertEqual(row["doc_date_text"], "Июль 2026 года")
        self.assertEqual(row["doc_date_iso"], "2026-07-01")

    def test_existing_act_without_override_does_not_gain_new_text(self):
        from acts_app.models import ActMaterialItem
        item = ActMaterialItem(passport=self.passport, manual_doc_date=date(2026, 7, 1))
        self.assertIn("01.07.2026", resolve_material_fields(item)["document_date_str"])
