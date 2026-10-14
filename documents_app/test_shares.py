import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from documents_app.models import BatchScan, BatchScanShare, DocumentBatch, DocumentBatchSelectionMode


class ScanShareTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="owner", email="owner@example.test", password="test")
        self.client.force_login(self.user)
        self.guest = Client()
        self.batch = DocumentBatch.objects.create(
            title="Комплект октября", comment="PRIVATE INTERNAL COMMENT", created_by=self.user,
            selection_mode=DocumentBatchSelectionMode.RANGE, month_from="10.2026", month_to="10.2026",
        )
        self.scan = self.make_scan()
        self.create_url = reverse("documents:batch_scan_share_create", args=[self.batch.pk])

    def make_scan(self, **kwargs):
        return BatchScan.objects.create(batch=self.batch, uploaded_by=self.user,
                                       name=kwargs.pop("name", "Сканы.zip"), size=1234,
                                       bucket="private-bucket", key=str(uuid.uuid4()),
                                       ready=kwargs.pop("ready", True), **kwargs)

    def share(self):
        response = self.client.post(self.create_url)
        self.assertEqual(response.status_code, 200)
        share = BatchScanShare.objects.latest("created_at")
        self.assertTrue(response.json()["url"].endswith(reverse("documents:scan_share", args=[share.pk])))
        return share

    def download_url(self, share, scan=None):
        return reverse("documents:scan_share_download", args=[share.pk, (scan or self.scan).pk])

    def test_share_lasts_seven_days_and_freezes_files_and_summary(self):
        pending = self.make_scan(ready=False)
        before = timezone.now()
        share = self.share()
        self.assertGreaterEqual(share.expires_at, before + timedelta(days=7))
        self.assertLessEqual(share.expires_at, timezone.now() + timedelta(days=7))
        self.assertEqual(list(share.scans.all()), [self.scan])
        self.assertNotIn(pending, share.scans.all())
        self.batch.title = "Changed later"
        self.batch.save()
        later = self.make_scan(name="Later.zip")
        response = self.guest.get(reverse("documents:scan_share", args=[share.pk]))
        self.assertContains(response, "Doc-helper облако")
        self.assertContains(response, "Комплект октября")
        self.assertContains(response, "10.2026")
        self.assertNotContains(response, "Changed later")
        self.assertNotContains(response, "PRIVATE INTERNAL COMMENT")
        self.assertNotContains(response, "private-bucket")
        self.assertNotContains(response, later.name)
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("noindex", response["X-Robots-Tag"])

    def test_no_ready_scans_does_not_create_link(self):
        self.scan.ready = False
        self.scan.save()
        self.assertEqual(self.client.post(self.create_url).status_code, 400)
        self.assertFalse(BatchScanShare.objects.exists())

    def test_creation_requires_change_permission_post_and_csrf(self):
        self.assertEqual(self.guest.post(self.create_url).status_code, 403)
        self.assertEqual(self.client.get(self.create_url).status_code, 405)
        viewer = get_user_model().objects.create_user(username="viewer", email="viewer@example.test")
        viewer.user_permissions.add(Permission.objects.get(codename="view_documentbatch"))
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(self.create_url).status_code, 403)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post(self.create_url).status_code, 403)
        self.assertFalse(BatchScanShare.objects.exists())

    @patch("documents_app.share_views.scan_storage.client")
    def test_guest_download_and_expiration(self, s3):
        s3.return_value.generate_presigned_url.return_value = "https://s3.example/signed"
        share = self.share()
        response = self.guest.get(self.download_url(share))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://s3.example/signed")
        self.assertEqual(s3.return_value.generate_presigned_url.call_args.kwargs["ExpiresIn"], 3600)
        share.expires_at = timezone.now() + timedelta(seconds=31)
        share.save()
        self.guest.get(self.download_url(share))
        self.assertLessEqual(s3.return_value.generate_presigned_url.call_args.kwargs["ExpiresIn"], 31)
        share.expires_at = timezone.now() - timedelta(seconds=1)
        share.save()
        s3.reset_mock()
        for url in [reverse("documents:scan_share", args=[share.pk]), self.download_url(share)]:
            response = self.guest.get(url)
            self.assertEqual(response.status_code, 410)
            self.assertNotContains(response, self.batch.title, status_code=410)
            self.assertNotContains(response, self.scan.name, status_code=410)
        s3.assert_not_called()

    @patch("documents_app.share_views.scan_storage.client")
    def test_other_and_deleted_files_are_not_accessible(self, s3):
        share = self.share()
        other = self.make_scan(name="Other.zip")
        self.assertEqual(self.guest.get(self.download_url(share, other)).status_code, 404)
        url = self.download_url(share)
        self.scan.delete()
        self.assertEqual(self.guest.get(url).status_code, 404)
        s3.assert_not_called()

    def test_unknown_link_and_escaped_names(self):
        self.assertEqual(self.guest.get(reverse("documents:scan_share", args=[uuid.uuid4()])).status_code, 404)
        self.scan.name = "<script>alert(1)</script>.zip"
        self.scan.save()
        share = self.share()
        response = self.guest.get(reverse("documents:scan_share", args=[share.pk]))
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>")

    def test_list_has_share_and_detail_breadcrumb_targets_list(self):
        response = self.client.get(reverse("documents:id_handover_batch_list"))
        self.assertContains(response, self.create_url)
        with patch("documents_app.views.DocumentBatchDetailView._build_preview_safe", return_value=None):
            response = self.client.get(reverse("documents:id_handover_batch_detail", args=[self.batch.pk]))
        self.assertContains(response, f'href="{reverse("documents:id_handover_batch_list")}">Комплекты</a>')
