import json
import logging
from pathlib import PurePosixPath

from botocore.exceptions import BotoCoreError, ClientError
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import content_disposition_header
from django.views import View

from documents_app.models import BatchScan, DocumentBatch
from documents_app.services import scan_storage as storage

logger = logging.getLogger(__name__)


class ScanUploadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.change_documentbatch"
    raise_exception = True

    def post(self, request, batch_id, scan_id=None, action="start"):
        try:
            data = json.loads(request.body or b"{}")
            if not isinstance(data, dict):
                raise ValueError("Некорректный запрос.")
            with transaction.atomic():
                # Serialize upload lifecycle and batch deletion for this batch.
                batch = get_object_or_404(DocumentBatch.objects.select_for_update(), pk=batch_id)
                s3 = storage.client()
                if action == "start":
                    name, size = data.get("name"), data.get("size")
                    storage.validate_file(name, size)
                    scan = BatchScan(batch=batch, uploaded_by=request.user, name=name,
                                     size=size, bucket=storage.option("BUCKET"))
                    scan.key = f"id-scans/{batch.uuid}/{scan.pk}{PurePosixPath(name).suffix.lower()}"
                    scan.save()
                    result = s3.create_multipart_upload(
                        Bucket=scan.bucket, Key=scan.key, ContentType="application/octet-stream",
                        ContentDisposition=content_disposition_header(True, scan.name),
                    )
                    scan.upload_id = result["UploadId"]
                    try:
                        scan.save(update_fields=["upload_id"])
                    except Exception:
                        s3.abort_multipart_upload(**storage.params(scan))
                        raise
                    return JsonResponse({"id": str(scan.pk), "part_size": storage.PART_SIZE})
                scan = get_object_or_404(BatchScan.objects.select_for_update(), pk=scan_id, batch=batch)
                if not scan.ready and scan.uploaded_by_id != request.user.pk and not request.user.is_superuser:
                    return JsonResponse({"error": "Эту загрузку начал другой пользователь."}, status=403)
                if action == "part":
                    if scan.ready:
                        raise ValueError("Файл уже загружен.")
                    number = data.get("number")
                    if type(number) is not int or not 1 <= number <= (scan.size + storage.PART_SIZE - 1) // storage.PART_SIZE:
                        raise ValueError("Некорректный номер части.")
                    url = s3.generate_presigned_url("upload_part", Params={
                        **storage.params(scan), "PartNumber": number,
                    }, ExpiresIn=3600)
                    return JsonResponse({"url": url})
                if action == "complete":
                    if not scan.ready:
                        # Recover if S3 completed but the HTTP response/DB commit was lost.
                        try:
                            parts = storage.verified_parts(s3, scan)
                        except ClientError as exc:
                            if exc.response["Error"]["Code"] != "NoSuchUpload":
                                raise
                        else:
                            s3.complete_multipart_upload(**storage.params(scan), MultipartUpload={"Parts": parts})
                        head = s3.head_object(Bucket=scan.bucket, Key=scan.key)
                        if head["ContentLength"] != scan.size:
                            raise ValueError("Размер файла в хранилище не совпадает с ожидаемым.")
                        scan.ready = True
                        scan.save(update_fields=["ready"])
                    return JsonResponse({"ok": True})
                if action == "delete":
                    if not scan.ready:
                        try:
                            s3.abort_multipart_upload(**storage.params(scan))
                        except ClientError as exc:
                            if exc.response["Error"]["Code"] != "NoSuchUpload":
                                raise
                    s3.delete_object(Bucket=scan.bucket, Key=scan.key)
                    scan.delete()
                    return JsonResponse({"ok": True})
                raise ValueError("Неизвестное действие.")
        except (ValueError, UnicodeDecodeError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except ImproperlyConfigured as exc:
            return JsonResponse({"error": str(exc)}, status=503)
        except (BotoCoreError, ClientError):
            logger.exception("S3 scan operation failed for batch %s", batch_id)
            return JsonResponse({"error": "Хранилище S3 недоступно. Попробуйте ещё раз."}, status=502)


class ScanDownloadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.view_documentbatch"
    raise_exception = True

    def get(self, request, batch_id, scan_id):
        scan = get_object_or_404(BatchScan.objects.select_related("batch"), pk=scan_id, batch_id=batch_id, ready=True)
        try:
            url = storage.client().generate_presigned_url("get_object", Params={
                "Bucket": scan.bucket, "Key": scan.key,
                "ResponseContentDisposition": content_disposition_header(True, storage.download_name(scan)),
                "ResponseContentType": "application/octet-stream",
            }, ExpiresIn=300)
        except (BotoCoreError, ClientError, ImproperlyConfigured):
            logger.exception("Could not sign scan download")
            return JsonResponse({"error": "Хранилище S3 недоступно."}, status=503)
        response = redirect(url)
        response["Cache-Control"] = "no-store"
        return response
