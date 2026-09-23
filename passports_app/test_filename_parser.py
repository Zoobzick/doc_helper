import io
import zipfile
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase

from passports_app.forms import PassportUpdateForm
from passports_app.models import Passport
from passports_app.parsers import parse_passport_filename
from passports_app.services import import_single_passport_file
from passports_app.services_archive import import_passports_from_zip


def filename(value):
    return f"Материал 10% (Паспорт №123%45%6 от {value}).pdf"


class FilenameParserTests(SimpleTestCase):
    def test_partial_dates_are_preserved_and_only_number_is_decoded(self):
        for value in ("07.2026", "07.2026г.", "Июль 2026 года", "2026", "26–27.07.2026", "Июль  2026 года"):
            with self.subTest(value=value):
                result = parse_passport_filename(filename(value))
                self.assertEqual(result.material, "Материал 10%")
                self.assertEqual(result.document_name, "Паспорт")
                self.assertEqual(result.document_number, "123/45/6")
                self.assertIsNone(result.document_date)
                self.assertEqual(result.document_date_text, value)
                self.assertFalse(result.needs_review)

    def test_calendar_dates_and_invalid_dates(self):
        for value, expected in (("15.07.2026", date(2026, 7, 15)), ("29.02.2024", date(2024, 2, 29)),
                                ("31.02.2026", None), ("29.02.2025", None), ("01.13.2026", None)):
            with self.subTest(value=value):
                result = parse_passport_filename(filename(value))
                self.assertEqual(result.document_date, expected)
                self.assertEqual(result.document_date_text, "" if expected else value)
                self.assertEqual(result.needs_review, expected is None)

    def test_unrecognized_filename(self):
        self.assertIsNone(parse_passport_filename("скан.pdf"))


class FilenameImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="parser")

    def import_file(self, value, **overrides):
        with patch("django.db.models.fields.files.FieldFile.save") as save:
            passport = import_single_passport_file(
                uploaded_file=SimpleUploadedFile(filename(value), b"test"), user=self.user, **overrides,
            )
            self.assertEqual(save.call_args.args[0], filename(value))
        return passport

    def test_single_import_and_manual_overrides(self):
        passport = self.import_file("07.2026г.")
        self.assertEqual(passport.document_number, "123/45/6")
        self.assertEqual(passport.document_date_text, "07.2026г.")
        self.assertIsNone(passport.document_date)
        self.assertFalse(passport.needs_review)
        manual = self.import_file("31.02.2026", document_number="manual%1", document_date_text="Июль 2026 года")
        self.assertEqual(manual.document_number, "manual%1")
        self.assertEqual(manual.document_date_text, "Июль 2026 года")
        self.assertFalse(manual.needs_review)
        exact = self.import_file("07.2026г.", document_date=date(2026, 8, 5))
        self.assertEqual(exact.document_date, date(2026, 8, 5))
        self.assertEqual(exact.document_date_text, "")

    def test_invalid_date_remains_flagged_after_edit(self):
        passport = self.import_file("31.02.2026")
        self.assertTrue(passport.needs_review)
        self.assertEqual(passport.document_date_text, "31.02.2026")
        form = PassportUpdateForm(instance=passport, data={
            "material_name": "Материал", "document_name": "Паспорт", "document_number": "123/45/6",
            "document_date_text": "31.02.2026", "sheets_count": 1,
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.save().needs_review)

    def test_zip_import_uses_same_rules(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            for value in ("15.07.2026", "07.2026г.", "31.02.2026"):
                archive.writestr("folder/" + filename(value), b"test")
        uploaded = SimpleUploadedFile("passports.zip", content.getvalue())
        with patch("django.db.models.fields.files.FieldFile.save"):
            stats, _ = import_passports_from_zip(archive_file=uploaded, user=self.user)
        self.assertEqual(stats["errors"], 0)
        self.assertEqual(stats["imported"], 2)
        self.assertEqual(stats["needs_review"], 1)
        self.assertEqual(Passport.objects.filter(document_number="123/45/6").count(), 3)
