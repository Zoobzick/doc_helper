from django.core.management.base import BaseCommand, CommandError

from backup_app.models import BackupRun
from backup_app.services import create_backup, create_filesystem_backup


class Command(BaseCommand):
    help = "Создать архивный бэкап БД и файлов doc-helper."

    def add_arguments(self, parser):
        parser.add_argument(
            "--trigger",
            choices=[choice.value for choice in BackupRun.Trigger] + [choice.value.lower() for choice in BackupRun.Trigger],
            default=BackupRun.Trigger.MANUAL,
        )
        parser.add_argument("--reason", default="", help="Короткое описание причины бэкапа.")
        parser.add_argument(
            "--no-db-record",
            action="store_true",
            help="Создать архив без записи BackupRun. Полезно до применения миграций.",
        )

    def handle(self, *args, **options):
        trigger = options["trigger"].upper()
        try:
            if options["no_db_record"]:
                result = create_filesystem_backup(
                    trigger=trigger,
                    reason=options["reason"],
                )
            else:
                result = create_backup(
                    trigger=trigger,
                    reason=options["reason"],
                )
        except Exception as exc:
            raise CommandError(f"Backup failed: {exc}") from exc

        run_label = f"#{result.run.pk}" if result.run else "without DB record"
        self.stdout.write(
            self.style.SUCCESS(
                f"Backup {run_label} created: {result.path} ({result.size_bytes} bytes)"
            )
        )
