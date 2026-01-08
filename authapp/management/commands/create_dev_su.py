from __future__ import annotations

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.conf import settings

if not settings.DEBUG:
    raise RuntimeError("create_dev_su is for development only")


class Command(BaseCommand):
    """
    Создаёт тестового superuser'а для разработки.

    ❗ Использовать ТОЛЬКО в dev.
    ❗ Данные захардкожены намеренно.
    """

    help = "Create development superuser (hardcoded credentials)"

    def handle(self, *args, **options):
        User = get_user_model()

        email = "admin@dev.local"
        password = "admin"
        username = "admin"
        first_name = "Dev"
        last_name = "Admin"

        if User.objects.filter(email=email).exists():
            self.stdout.write(
                self.style.WARNING(f"Dev superuser already exists: {email}")
            )
            return

        user = User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )

        # на всякий случай (create_superuser и так это делает)
