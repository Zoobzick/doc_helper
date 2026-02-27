from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from projects_app.models import Project
from projects_app.services import sync_needs_review


class Command(BaseCommand):
    help = "Recompute Project.needs_review for all projects using current REQUIRED_PROJECT_FIELDS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do not save changes, only show how many would change.",
        )
        parser.add_argument(
            "--only-missing-address",
            action="store_true",
            help="Process only projects where address is empty/NULL.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        only_missing_address: bool = options["only_missing_address"]

        qs = Project.objects.all()

        if only_missing_address:
            qs = qs.filter(address__isnull=True) | qs.filter(address="")

        total = qs.count()
        changed = 0

        self.stdout.write(f"Projects to process: {total}")

        for project in qs.iterator(chunk_size=500):
            old_val = project.needs_review
            new_val = sync_needs_review(project, save=not dry_run)
            if new_val != old_val:
                changed += 1

        if dry_run:
            # откатим транзакцию, чтобы ничего не сохранилось
            raise transaction.TransactionManagementError(
                f"DRY RUN finished. Would change: {changed} projects out of {total}."
            )

        self.stdout.write(self.style.SUCCESS(f"Done. Changed: {changed} projects out of {total}."))