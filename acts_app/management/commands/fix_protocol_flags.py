from django.core.management.base import BaseCommand
from acts_app.models import Attachment


class Command(BaseCommand):
    help = "Проставляет флаги is_protocol и is_original для существующих протоколов"

    def handle(self, *args, **options):

        attachments = Attachment.objects.all()

        updated_protocols = 0
        checked = 0

        for a in attachments:
            checked += 1

            title = (a.title or "").lower()

            if "протокол" in title:
                if not a.is_protocol or not a.is_original:
                    a.is_protocol = True
                    a.is_original = True
                    a.save(update_fields=["is_protocol", "is_original"])
                    updated_protocols += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Проверено записей: {checked}\n"
                f"Обновлено протоколов: {updated_protocols}"
            )
        )