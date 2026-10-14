from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.template.loader import render_to_string
from django.contrib.auth.context_processors import PermWrapper
from django.urls import reverse

from documents_app.models import BatchScan, DocumentBatch
from documents_app.services.scan_storage import PART_SIZE, MAX_SIZE


@override_settings(S3_SCAN_BUCKET="test-scans")
class BatchScanTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="scan-admin", password="test")
        self.client.force_login(self.user)
        self.batch = DocumentBatch.objects.create(created_by=self.user)
        self.s3 = Mock()
        self.s3.create_multipart_upload.return_value = {"UploadId": "upload-1"}
        self.s3.generate_presigned_url.return_value = "https://storage.example/signed"
        self.s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        self.patcher = patch("documents_app.services.scan_storage.client", return_value=self.s3)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def start(self, size=100, name="Скан ИД.zip"):
        return self.client.post(reverse("documents:batch_scan_start", args=[self.batch.pk]),
                                {"name": name, "size": size}, content_type="application/json")

    def action(self, scan, action, data=None, batch=None):
        return self.client.post(reverse("documents:batch_scan_action", args=[
            (batch or self.batch).pk, scan.pk, action]), data or {}, content_type="application/json")

    def test_large_archive_only_creates_metadata_and_scoped_s3_upload(self):
        self.assertEqual(self.start(1600 * 1024**2).status_code, 200)
        scan = BatchScan.objects.get()
        self.assertFalse(scan.ready)
        self.assertEqual(scan.bucket, "test-scans")
        self.assertTrue(scan.key.startswith(f"id-scans/{self.batch.uuid}/"))
        self.assertEqual(self.action(scan, "part", {"number": 1}).status_code, 200)
        self.assertEqual(self.action(scan, "part", {"number": 10000}).status_code, 400)
        self.s3.upload_file.assert_not_called()

    def test_invalid_files_do_not_start_s3_upload(self):
        for name, size in [("x.zip", 0), ("x.zip", MAX_SIZE + 1), ("../x.zip", 1),
                           ("x.exe", 10), ("x.zip", True), ("x.zip", "100")]:
            with self.subTest(name=name, size=size):
                self.assertEqual(self.start(size, name).status_code, 400)
        self.s3.create_multipart_upload.assert_not_called()

    def test_complete_checks_remote_parts_and_is_idempotent(self):
        self.start(PART_SIZE + 20)
        scan = BatchScan.objects.get()
        self.s3.get_paginator.return_value.paginate.return_value = [{"Parts": [
            {"PartNumber": 1, "Size": PART_SIZE, "ETag": "one"},
            {"PartNumber": 2, "Size": 20, "ETag": "two"},
        ]}]
        self.s3.head_object.side_effect = None
        self.s3.head_object.return_value = {"ContentLength": scan.size}
        self.assertEqual(self.action(scan, "complete").status_code, 200)
        scan.refresh_from_db()
        self.assertTrue(scan.ready)
        self.assertEqual(self.action(scan, "complete").status_code, 200)
        self.s3.complete_multipart_upload.assert_called_once()

    def test_wrong_size_cannot_be_published(self):
        self.start()
        scan = BatchScan.objects.get()
        self.s3.get_paginator.return_value.paginate.return_value = [{"Parts": [
            {"PartNumber": 1, "Size": 99, "ETag": "one"}]}]
        self.assertEqual(self.action(scan, "complete").status_code, 400)
        scan.refresh_from_db()
        self.assertFalse(scan.ready)
        self.s3.complete_multipart_upload.assert_not_called()

    def test_lost_completion_response_is_recovered(self):
        self.start()
        scan = BatchScan.objects.get()
        self.s3.head_object.side_effect = None
        self.s3.head_object.return_value = {"ContentLength": scan.size}
        self.s3.get_paginator.return_value.paginate.side_effect = ClientError(
            {"Error": {"Code": "NoSuchUpload"}}, "ListParts")
        self.assertEqual(self.action(scan, "complete").status_code, 200)
        self.s3.complete_multipart_upload.assert_not_called()
        scan.refresh_from_db()
        self.assertTrue(scan.ready)

    def test_cancel_aborts_upload_and_removes_metadata(self):
        self.start()
        scan = BatchScan.objects.get()
        self.assertEqual(self.action(scan, "delete").status_code, 200)
        self.s3.abort_multipart_upload.assert_called_once()
        self.assertFalse(BatchScan.objects.exists())

    def test_s3_failure_keeps_record_for_retry(self):
        self.start()
        scan = BatchScan.objects.get()
        self.s3.abort_multipart_upload.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "AbortMultipartUpload")
        with self.assertLogs("documents_app.scan_views", level="ERROR"):
            self.assertEqual(self.action(scan, "delete").status_code, 502)
        self.assertTrue(BatchScan.objects.exists())

    def test_cross_batch_and_permissions(self):
        self.start()
        scan = BatchScan.objects.get()
        other = DocumentBatch.objects.create(created_by=self.user)
        self.assertEqual(self.action(scan, "delete", batch=other).status_code, 404)
        viewer = get_user_model().objects.create_user(username="viewer", email="viewer@example.test")
        viewer.user_permissions.add(Permission.objects.get(codename="view_documentbatch"))
        self.client.force_login(viewer)
        self.assertEqual(self.start().status_code, 403)
        url = reverse("documents:batch_scan_download", args=[self.batch.pk, scan.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        scan.ready = True
        scan.save()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_batch_deletion_keeps_scan_and_batch(self):
        self.start()
        response = self.client.post(reverse("documents:id_handover_batch_delete", args=[self.batch.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(DocumentBatch.objects.filter(pk=self.batch.pk).exists())
        self.assertTrue(BatchScan.objects.exists())

    def test_template_renders_upload_download_and_escaped_name(self):
        self.start(name="<скан>.zip")
        scan = BatchScan.objects.get()
        scan.ready = True
        scan.save()
        html = render_to_string("documents_app/id_handover/batch_scans.html", {
            "batch": self.batch, "user": self.user, "perms": PermWrapper(self.user),
        })
        self.assertIn("Загрузить скан ИД", html)
        self.assertIn("&lt;скан&gt;.zip", html)
        self.assertIn(reverse("documents:batch_scan_download", args=[self.batch.pk, scan.pk]), html)

    def test_csrf_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(reverse("documents:batch_scan_start", args=[self.batch.pk]),
                               {"name": "x.zip", "size": 10}, content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.s3.create_multipart_upload.assert_not_called()

    def test_other_editor_cannot_cancel_pending_upload(self):
        self.start()
        scan = BatchScan.objects.get()
        editor = get_user_model().objects.create_user(username="editor", email="editor@example.test")
        editor.user_permissions.add(Permission.objects.get(codename="change_documentbatch"))
        self.client.force_login(editor)
        self.assertEqual(self.action(scan, "delete").status_code, 403)
        self.s3.abort_multipart_upload.assert_not_called()
