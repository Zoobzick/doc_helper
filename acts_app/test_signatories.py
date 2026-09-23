from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from acts_app.models import Act, ActParty
from acts_app.services.act_docx_context import _resolve_authorization
from acts_app.services.act_docx_generator import DocxRenderError, generate_act_docx
from acts_app.services.signatories import (
    choose_authorization_for_party, resolve_party, validate_before_finalize,
)
from acts_app.views import ensure_default_parties_for_act
from directive_app.models import ActRole, Authorization, Directive
from orgs_app.models import Organization, Person


class SignatoryChoiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="signer", password="test")
        self.org = Organization.objects.create(short_name="Test", full_name="Test", ogrn="1")
        person = Person.objects.create(last_name="Test", first_name="Signer")
        directive = Directive.objects.create(number="1", date=date(2026, 1, 1), issuer_organization=self.org)
        self.auths = [Authorization.objects.create(
            organization=self.org, person=person, directive=directive,
            role=ActRole.BUILDER_REP, valid_from=date(2026, month, 1),
        ) for month in (1, 2)]
        self.act = self.new_act("1")
        self.party = ActParty.objects.create(
            act=self.act, role=ActRole.BUILDER_REP, organization=self.org,
            chosen_authorization=self.auths[0],
        )

    def new_act(self, number):
        return Act.objects.create(number=number, act_date=date(2026, 3, 1), created_by=self.user)

    def inherited_party(self):
        act = self.new_act("next")
        ensure_default_parties_for_act(act=act, user=self.user)
        return act.parties.get(role=ActRole.BUILDER_REP)

    def test_older_choice_is_inherited_instead_of_newest_candidate(self):
        party = self.inherited_party()
        self.assertEqual(party.chosen_authorization, self.auths[0])
        self.assertTrue(party.authorization_inherited)
        self.assertEqual(resolve_party(party, party.act.act_date).effective_authorization, self.auths[0])
        self.assertEqual(_resolve_authorization(party.act, party.role, self.org.id, party.chosen_authorization), self.auths[0])
        html = render_to_string("acts_app/partials/act_party_row.html", {
            "party": party, "resolved": resolve_party(party, party.act.act_date),
        })
        self.assertIn("Выбрано по предыдущему акту", html)
        self.assertIn("Изменить", html)
        choose_authorization_for_party(party=party, authorization_id=self.auths[1].id)
        party.refresh_from_db()
        self.assertFalse(party.authorization_inherited)
        self.assertEqual(self.inherited_party().chosen_authorization, self.auths[1])

    def test_expired_choice_falls_back_to_only_valid_candidate(self):
        self.auths[0].valid_to = date(2026, 2, 28)
        self.auths[0].save()
        party = self.inherited_party()
        self.assertIsNone(party.chosen_authorization)
        self.assertEqual(resolve_party(party, party.act.act_date).effective_authorization, self.auths[1])

    def test_inactive_choice_is_not_inherited(self):
        self.auths[0].is_active = False
        self.auths[0].save()
        self.assertIsNone(self.inherited_party().chosen_authorization)

    def test_missing_choice_blocks_document_before_file_work(self):
        self.party.chosen_authorization = None
        self.party.save()
        party = self.inherited_party()
        self.assertEqual(resolve_party(party, party.act.act_date).status, "MANY_AUTH_NEED_CHOICE")
        self.assertIsNone(_resolve_authorization(party.act, party.role, self.org.id, None))
        with self.assertRaises(ValidationError):
            validate_before_finalize(party.act)
        with patch("acts_app.services.act_docx_generator.AppendixBuilder") as builder:
            with self.assertRaisesMessage(DocxRenderError, "нужен ручной выбор"):
                generate_act_docx(party.act)
            builder.assert_not_called()

    def test_another_users_choice_is_not_inherited(self):
        self.act.created_by = get_user_model().objects.create_user(username="other", email="other@example.com")
        self.act.save()
        party = self.inherited_party()
        self.assertIsNone(party.organization)
        self.assertIsNone(party.chosen_authorization)

    def test_duplicate_inherits_choice(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("acts_app:act_duplicate", kwargs={"uuid": self.act.uuid}))
        self.assertEqual(response.status_code, 302)
        duplicate = Act.objects.exclude(pk=self.act.pk).get()
        party = duplicate.parties.get(role=self.party.role)
        self.assertEqual(party.chosen_authorization, self.auths[0])
        self.assertTrue(party.authorization_inherited)

    def test_downloads_do_not_serve_cached_files_when_choice_is_missing(self):
        self.party.chosen_authorization = None
        self.party.save()
        self.client.force_login(self.user)
        for route in ("act_docx_download", "act_pdf_preview"):
            with self.subTest(route=route), patch("acts_app.views.get_act_docx_paths") as paths:
                response = self.client.get(reverse("acts_app:" + route, kwargs={"uuid": self.act.uuid}))
                self.assertGreaterEqual(response.status_code, 400)
                self.assertIn("нужен ручной выбор", response.content.decode())
                paths.assert_not_called()

    def test_bulk_export_does_not_reuse_cached_act(self):
        from acts_app.services.bulk_export import _existing_or_generated_act_paths
        self.party.chosen_authorization = None
        self.party.save()
        with self.assertRaises(DocxRenderError):
            _existing_or_generated_act_paths(self.act)

    def test_invalid_inheritance_checks_role_organization_and_date(self):
        from acts_app.services.signatories import inherit_authorization
        for overrides in (
            {"role": ActRole.OTHER_REP}, {"organization_id": None}, {"is_enabled": False},
        ):
            with self.subTest(overrides=overrides):
                values = dict(role=self.party.role, organization_id=self.org.id)
                values.update(overrides)
                party = ActParty(**values)
                inherit_authorization(party, self.party, self.act.act_date)
                self.assertIsNone(party.chosen_authorization)
        party = ActParty(role=self.party.role, organization_id=self.org.id)
        inherit_authorization(party, self.party, date(2025, 12, 31))
        self.assertIsNone(party.chosen_authorization)

    def test_multiple_other_representatives_keep_their_own_choices(self):
        for index, auth in enumerate(self.auths, start=4):
            auth.role = ActRole.OTHER_REP
            auth.save()
            ActParty.objects.create(act=self.act, role=ActRole.OTHER_REP,
                                    organization=self.org, position=index, chosen_authorization=auth)
        act = self.new_act("other-reps")
        ensure_default_parties_for_act(act=act, user=self.user)
        self.assertEqual(list(act.parties.filter(role=ActRole.OTHER_REP)
                              .values_list("chosen_authorization_id", flat=True)),
                         [auth.id for auth in self.auths])

    def test_date_change_clears_choice_and_inherited_label(self):
        from acts_app.services.signatories import reset_choices_for_act_on_date_change
        party = self.inherited_party()
        reset_choices_for_act_on_date_change(party.act)
        party.refresh_from_db()
        self.assertIsNone(party.chosen_authorization)
        self.assertFalse(party.authorization_inherited)
