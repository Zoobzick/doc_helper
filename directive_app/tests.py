from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from orgs_app.models import Organization

from .models import Directive, DirectiveShareLink


class DirectiveListShareTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="directive-share-user",
            email="directive-share@example.com",
            first_name="Тест",
            last_name="Пользователь",
            password="test-password",
        )
        cls.organization = Organization.objects.create(
            full_name="Тестовая организация",
            short_name="Тест",
            ogrn="1234567890123",
            inn="1234567890",
            address="Тестовый адрес",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_directive_urls_use_single_prefix_and_legacy_list_still_works(self):
        self.assertEqual(reverse("directive_app:directive_list"), "/directives/")
        self.assertEqual(
            reverse("directive_app:directive_shared_open", kwargs={"token": "test-token"}),
            "/directives/shared/test-token/",
        )
        self.assertEqual(self.client.get("/directives/directives/").status_code, 200)

    def test_share_button_copies_directive_data_when_file_is_missing(self):
        Directive.objects.create(
            doc_type="ORDER",
            number="42",
            date=date(2026, 7, 28),
            issuer_organization=self.organization,
        )

        response = self.client.get(reverse("directive_app:directive_list"))

        self.assertContains(response, "data-directive-share")
        self.assertContains(response, 'data-share-text="Приказ №42 от 28.07.2026"')
        self.assertNotContains(response, "data-share-url=")

    def test_share_button_creates_temporary_link_when_file_exists(self):
        directive = Directive.objects.create(
            doc_type="ORDER",
            number="43",
            date=date(2026, 7, 28),
            issuer_organization=self.organization,
            pdf_file="Тест/Приказ-43.pdf",
        )

        response = self.client.get(reverse("directive_app:directive_list"))

        self.assertContains(
            response,
            f'data-share-url="{reverse("directive_app:directive_share_link", kwargs={"uuid": directive.uuid})}"',
        )

        share_response = self.client.post(
            reverse("directive_app:directive_share_link", kwargs={"uuid": directive.uuid})
        )

        self.assertEqual(share_response.status_code, 200)
        link = DirectiveShareLink.objects.get(directive=directive)
        self.assertIn(
            reverse("directive_app:directive_shared_open", kwargs={"token": link.token}),
            share_response.json()["url"],
        )
