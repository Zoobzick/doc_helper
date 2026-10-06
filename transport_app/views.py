import logging
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models import Q, Exists, OuterRef
from django.http import HttpResponse
from django.utils.http import content_disposition_header
from django.shortcuts import get_object_or_404, redirect, render
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .forms import Items, RequestForm
from .models import TransportUserSettings, TransportEvent, TransportPDF, TransportRequest
from .services import generate_pdf

logger = logging.getLogger(__name__)


@login_required
@permission_required("transport_app.view_transportrequest", raise_exception=True)
def request_list(request):
    objects = TransportRequest.objects.prefetch_related("items").annotate(
        has_current_pdf=Exists(TransportPDF.objects.filter(request_id=OuterRef("pk"), revision=OuterRef("revision")))
    )
    query = request.GET.get("q", "").strip()
    if query:
        # Match “автокран 50т.” and “Автокран 50 т” alike. Exists avoids duplicate requests.
        terms = re.findall(r"[^\W\d_]+|\d+", query, flags=re.UNICODE)
        vehicle_pattern = r"[\s.]*".join(re.escape(term) for term in terms)
        filters = Q(place__icontains=query) | Q(site__icontains=query) | Q(organization__icontains=query)
        if vehicle_pattern:
            matching_items = TransportRequest.objects.filter(pk=OuterRef("pk"), items__vehicle__iregex=vehicle_pattern)
            objects = objects.annotate(matches_vehicle=Exists(matching_items))
            filters |= Q(matches_vehicle=True)
        objects = objects.filter(filters)
    return render(request, "transport_app/list.html", {"page": Paginator(objects, 25).get_page(request.GET.get("page")), "q": query})


@login_required
def edit(request, pk=None):
    saved = None
    required = "change" if pk else "add"
    if not request.user.has_perm(f"transport_app.{required}_transportrequest"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    with transaction.atomic():
        obj = get_object_or_404(TransportRequest.objects.select_for_update(), pk=pk) if pk else TransportRequest(created_by=request.user)
        defaults = TransportUserSettings.objects.filter(user=request.user).first() if not pk else None
        initial = defaults.values if defaults else {}
        form = RequestForm(request.POST if request.method == "POST" else None, instance=obj, initial=initial)
        items = Items(request.POST if request.method == "POST" else None, instance=obj, prefix="items")
        if request.method == "POST":
            valid_form, valid_items = form.is_valid(), items.is_valid()
            if valid_form and pk and form.cleaned_data["version"] != obj.revision:
                form.add_error(None, "Заявку уже изменили. Откройте её заново, чтобы не перезаписать изменения.")
                valid_form = False
            if valid_form and valid_items:
                obj = form.save(commit=False)
                if pk:
                    obj.revision += 1
                obj.save()
                items.instance = obj
                items.save()
                if pk:
                    obj.pdfs.all().delete()
                TransportUserSettings.objects.update_or_create(user=request.user, defaults={"values": {
                    key: form.cleaned_data[key] for key in RequestForm.Meta.fields if key != "date"
                }})
                TransportEvent.objects.create(request=obj, user=request.user, text=f"{'Изменена' if pk else 'Создана'} заявка, версия {obj.revision}")
                saved = obj
    if saved is not None:
        return _generate_pdf_response(request, saved)
    start_step = 2 if pk and request.method == "GET" else 1
    if request.method == "GET" and not pk and defaults and defaults.skip_general:
        default_data = {**defaults.values, "date": obj.date, "version": 1}
        if RequestForm(data=default_data).is_valid():
            start_step = 2
    if request.method == "POST" and not form.errors:
        start_step = 2
    return render(request, "transport_app/form.html", {"form": form, "items": items, "object": obj, "start_step": start_step})


@login_required
@permission_required("transport_app.view_transportrequest", raise_exception=True)
def detail(request, pk):
    obj = get_object_or_404(TransportRequest.objects.prefetch_related("items"), pk=pk)
    return render(request, "transport_app/detail.html", {"object": obj, "pdf": obj.pdfs.filter(revision=obj.revision).defer("content").first(), "events": obj.events.exclude(text__startswith="Статус:").select_related("user")})


@login_required
@permission_required(("transport_app.view_transportrequest", "transport_app.change_transportrequest"), raise_exception=True)
@require_POST
def create_pdf(request, pk):
    obj = get_object_or_404(TransportRequest.objects.prefetch_related("items"), pk=pk)
    if str(obj.revision) != request.POST.get("version"):
        messages.error(request, "Заявка изменена. Обновите страницу перед формированием PDF.")
        return redirect("transport:detail", pk=pk)
    return _generate_pdf_response(request, obj)


def _generate_pdf_response(request, obj):
    pk = obj.pk
    if not obj.items.exists():
        messages.error(request, "Добавьте хотя бы одну позицию транспорта.")
        return redirect("transport:detail", pk=pk)
    if obj.pdfs.filter(revision=obj.revision).exists():
        if request.POST.get("action") == "download":
            return redirect("transport:download", pk=pk, revision=obj.revision)
        return redirect("transport:detail", pk=pk)
    try:
        content = generate_pdf(obj)
    except (RuntimeError, OSError):
        logger.exception("Transport PDF generation failed for request %s", pk)
        messages.error(request, "Не удалось сформировать PDF. Данные заявки сохранены. Проверьте доступность LibreOffice на сервере и повторите попытку.")
        return redirect("transport:detail", pk=pk)
    with transaction.atomic():
        current = TransportRequest.objects.select_for_update().get(pk=pk)
        if current.revision != obj.revision:
            messages.error(request, "Во время формирования заявку изменили. Сформируйте PDF заново.")
        else:
            pdf, created = TransportPDF.objects.get_or_create(request=current, revision=current.revision, defaults={"content": content, "created_by": request.user})
            if created:
                TransportEvent.objects.create(request=current, user=request.user, text=f"Сформирован PDF, версия {current.revision}")
            if request.POST.get("action") == "download":
                return redirect("transport:download", pk=pk, revision=current.revision)
            messages.success(request, "PDF готов к скачиванию.")
    return redirect("transport:detail", pk=pk)


@login_required
@permission_required("transport_app.view_transportrequest", raise_exception=True)
def download(request, pk, revision):
    pdf = get_object_or_404(TransportPDF.objects.select_related("request").prefetch_related("request__items"), request_id=pk, revision=revision, request__revision=revision)
    response = HttpResponse(bytes(pdf.content), content_type="application/pdf")
    period = pdf.request.period.replace(" — ", "-")
    filename = f"Заявка от {pdf.request.date:%d.%m.%Y} (на {period}).pdf"
    response["Content-Disposition"] = content_disposition_header(request.GET.get("preview") != "1", filename)
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_http_methods(["GET", "POST"])
def delete(request, pk):
    if not (request.user.is_superuser or (request.user.is_staff and request.user.has_perm("transport_app.delete_transportrequest"))):
        raise PermissionDenied
    with transaction.atomic():
        obj = get_object_or_404(TransportRequest.objects.select_for_update(), pk=pk)
        if request.method == "POST":
            if request.POST.get("version") != str(obj.revision):
                messages.error(request, "Заявку изменили. Проверьте её перед удалением.")
                return redirect("transport:delete", pk=pk)
            obj.delete()
            messages.success(request, f"Заявка № {pk} удалена.")
            return redirect("transport:list")
    return render(request, "transport_app/delete.html", {"object": obj})


