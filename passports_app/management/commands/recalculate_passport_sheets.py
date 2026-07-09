from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand

from passports_app.models import Passport
from passports_app.services import get_physical_sheets_for_pdf_path


class Command(BaseCommand):
    help = "Recalculate Passport.sheets_count for PDF files using duplex printing rules."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write calculated values. Without this flag the command only prints a report.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Recalculate passports even when sheets_count is not 1.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Process only the first N PDF passports. Useful for a small trial run.",
        )

    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        overwrite = bool(options["overwrite"])
        limit = max(int(options["limit"] or 0), 0)

        qs = (
            Passport.objects
            .filter(file_ext__iexact="pdf")
            .only("id", "file", "sheets_count", "document_name", "document_number")
            .order_by("id")
        )
        if limit:
            qs = qs[:limit]

        stats = {
            "processed": 0,
            "would_change": 0,
            "updated": 0,
            "same": 0,
            "skipped_manual": 0,
            "missing_file": 0,
            "errors": 0,
        }
        samples: list[str] = []
        error_samples: list[str] = []

        for passport in qs.iterator():
            stats["processed"] += 1

            current_sheets = int(passport.sheets_count or 1)
            if current_sheets != 1 and not overwrite:
                stats["skipped_manual"] += 1
                continue

            if not passport.file:
                stats["missing_file"] += 1
                continue

            try:
                pdf_path = Path(passport.file.path)
            except Exception as exc:
                stats["errors"] += 1
                self._append_sample(error_samples, passport, f"path error: {exc}")
                continue

            if not pdf_path.exists():
                stats["missing_file"] += 1
                self._append_sample(error_samples, passport, f"missing file: {pdf_path}")
                continue

            try:
                calculated_sheets = get_physical_sheets_for_pdf_path(pdf_path)
            except Exception as exc:
                stats["errors"] += 1
                self._append_sample(error_samples, passport, f"read error: {exc}")
                continue

            if calculated_sheets == current_sheets:
                stats["same"] += 1
                continue

            stats["would_change"] += 1
            self._append_sample(samples, passport, f"{current_sheets} -> {calculated_sheets}")

            if apply_changes:
                passport.sheets_count = calculated_sheets
                passport.save(update_fields=["sheets_count"])
                stats["updated"] += 1

        mode = "APPLY" if apply_changes else "DRY RUN"
        self.stdout.write(self.style.MIGRATE_HEADING(f"Passport sheets recalculation: {mode}"))
        self.stdout.write(f"Processed PDF passports: {stats['processed']}")
        self.stdout.write(f"Same value: {stats['same']}")
        self.stdout.write(f"Would change: {stats['would_change']}")
        if apply_changes:
            self.stdout.write(self.style.SUCCESS(f"Updated: {stats['updated']}"))
        self.stdout.write(f"Skipped manual values: {stats['skipped_manual']}")
        self.stdout.write(f"Missing files: {stats['missing_file']}")
        self.stdout.write(f"Errors: {stats['errors']}")

        if samples:
            self.stdout.write("")
            self.stdout.write("Change samples:")
            for sample in samples:
                self.stdout.write(f"  {sample}")

        if error_samples:
            self.stdout.write("")
            self.stdout.write("File/error samples:")
            for sample in error_samples:
                self.stdout.write(f"  {sample}")

        if not apply_changes:
            self.stdout.write("")
            self.stdout.write("Run with --apply to save Passport.sheets_count. Existing acts are not changed.")

    @staticmethod
    def _append_sample(samples: list[str], passport: Passport, note: str) -> None:
        if len(samples) >= 20:
            return
        doc_no = (passport.document_number or "").strip()
        suffix = f" #{doc_no}" if doc_no else ""
        samples.append(f"Passport {passport.pk}: {passport.document_name}{suffix}: {note}")
