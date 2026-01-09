from __future__ import annotations

import os
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Create default superuser if not exists"

    def handle(self, *args, **options):
        User = get_user_model()

        email = os.getenv("DJANGO_SU_EMAIL", "admin@example.com")
        password = os.getenv("DJANGO_SU_PASSWORD", "admin")
        username = os.getenv("DJANGO_SU_USERNAME", "admin")

        if User.objects.filter(email=email).exists():
            self.stdout.write(self.style.SUCCESS(f"Superuser already exists: {email}"))
            return

        User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
            first_name=os.getenv("DJANGO_SU_FIRST_NAME", "Admin"),
            last_name=os.getenv("DJANGO_SU_LAST_NAME", "Admin"),
        )
        self.stdout.write(self.style.SUCCESS(f"Superuser created: {email}"))
