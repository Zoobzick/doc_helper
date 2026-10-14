import logging
from datetime import timedelta

from botocore.exceptions import BotoCoreError, ClientError
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views import View

from documents_app.models import BatchScanShare, DocumentBatch, DocumentBatchSelectionMode
from documents_app.services import scan_storage

logger = logging.getLogger(__name__)


def share_summary(batch):
    if batch.selection_mode == DocumentBatchSelectionMode.RANGE:
        period = batch.month_from if batch.month_from == batch.month_to else f"{batch.month_from} — {batch.month_to}"
        period = period or "Период не задан"
    else:
        period = "За весь период"
    projects = list(batch.batch_projects.order_by("order", "id").values_list("project__full_code", flat=True))
    return {
        "title": batch.title or "Комплект ИД",
        "period": period,
        "projects": projects,
        "project_count": len(projects),
        "act_count": batch.batch_acts.values("act_id").distinct().count(),
    }


class CreateScanShareView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.change_documentbatch"
    raise_exception = True

    def post(self, request, batch_id):
        with transaction.atomic():
            batch = get_object_or_404(DocumentBatch.objects.select_for_update(), pk=batch_id)
            scans = list(batch.scans.filter(ready=True))
            if not scans:
                return JsonResponse({"error": "В комплекте нет загруженных сканов ИД. Сначала загрузите скан в карточке комплекта."}, status=400)
            share = BatchScanShare.objects.create(
                batch=batch, created_by=request.user,
                expires_at=timezone.now() + timedelta(days=7), summary=share_summary(batch),
            )
            share.scans.set(scans)
        response = JsonResponse({
            "url": request.build_absolute_uri(reverse("documents:scan_share", args=[share.pk])),
            "expires_at": share.expires_at.isoformat(),
        })
        response["Cache-Control"] = "no-store"
        return response


class PublicShareView(View):
    """Anonymous access is granted by an unguessable UUID, checked on every request."""

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response["Cache-Control"] = "no-store, private"
        response["Referrer-Policy"] = "no-referrer"
        response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    def get(self, request, token, scan_id=None):
        share = BatchScanShare.objects.filter(pk=token).first()
        if not share:
            return render(request, "documents_app/share.html", {"unavailable": "Ссылка недоступна", "explanation": "Проверьте адрес или попросите отправителя поделиться сканами ещё раз."}, status=404)
        remaining = int((share.expires_at - timezone.now()).total_seconds())
        if remaining <= 0:
            return render(request, "documents_app/share.html", {"unavailable": "Срок доступа истёк", "explanation": "Эта ссылка действовала одну неделю. Попросите отправителя создать новую."}, status=410)
        scans = share.scans.filter(ready=True, batch_id=share.batch_id)
        if scan_id is None:
            return render(request, "documents_app/share.html", {"share": share, "summary": share.summary, "scans": scans})
        scan = scans.filter(pk=scan_id).first()
        if not scan:
            return render(request, "documents_app/share.html", {"unavailable": "Файл недоступен", "explanation": "Возможно, отправитель удалил этот скан."}, status=404)
        try:
            url = scan_storage.client().generate_presigned_url("get_object", Params={
                "Bucket": scan.bucket, "Key": scan.key,
                "ResponseContentDisposition": content_disposition_header(True, scan.name),
                "ResponseContentType": "application/octet-stream",
            }, ExpiresIn=min(3600, remaining))
        except (BotoCoreError, ClientError, ImproperlyConfigured):
            logger.exception("Shared scan download signing failed")
            return render(request, "documents_app/share.html", {"unavailable": "Скачивание временно недоступно", "explanation": "Попробуйте открыть ссылку ещё раз через несколько минут."}, status=503)
        return redirect(url)
