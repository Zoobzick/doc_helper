from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from orgs_app.models import Organization
from projects_app.models import Line, Stage


class ProfileViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="user@example.com",
            email="user@example.com",
            password="password",
            first_name="Иван",
            last_name="Петров",
        )
        self.organization = Organization.objects.create(
            full_name="Общество с ограниченной ответственностью Тест",
            short_name="ООО Тест",
            ogrn="1234567890123",
            inn="1234567890",
            address="Тестовый адрес",
        )
        self.line = Line.objects.create(code="Л1", full_name="Линия 1")
        self.stage = Stage.objects.create(code="Этап 1", full_name="Этап 1")

    def test_profile_updates_user_scope(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("authapp:profile"),
            {
                "first_name": "Иван",
                "last_name": "Сидоров",
                "organization": self.organization.pk,
                "lines": [self.line.pk],
                "stages": [self.stage.pk],
            },
        )

        self.assertRedirects(response, reverse("authapp:profile"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_name, "Сидоров")
        self.assertEqual(self.user.organization, self.organization)
        self.assertEqual(list(self.user.lines.values_list("pk", flat=True)), [self.line.pk])
        self.assertEqual(list(self.user.stages.values_list("pk", flat=True)), [self.stage.pk])
