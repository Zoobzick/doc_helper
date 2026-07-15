from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.db.models import F
from django.http import FileResponse, Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from .forms import PassportUploadForm, PassportUpdateForm
from .models import Passport, Material, PassportShareLink
from .services import import_single_passport_file
from .services_archive import import_passports_from_zip


def _ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower().lstrip(".")


def _safe_download_name_part(value: str, fallback: str) -> str:
    value = " ".join((value or "").strip().split()) or fallback
    value = "".join("_" if ch in '<>:"/\\|?*' else ch for ch in value)
    return value.strip(" .") or fallback


def _passport_download_filename(passport: Passport, file_path: Path) -> str:
    material_name = _safe_download_name_part(
        getattr(getattr(passport, "material", None), "name", "") or "",
        "Материал",
    )
    document_name = _safe_download_name_part(passport.document_name or "", "Паспорт")
    document_date = passport.document_date.strftime("%d.%m.%Y") if passport.document_date else "без даты"
    ext = (passport.file_ext or file_path.suffix.lstrip(".") or _ext(file_path.name)).lower()
    suffix = f".{ext}" if ext else ""
    return f"{material_name} ({document_name} от {document_date}){suffix}"


def _passport_browser_title(passport: Passport, file_path: Path) -> str:
    filename = _passport_download_filename(passport, file_path)
    suffix = file_path.suffix
    if suffix and filename.lower().endswith(suffix.lower()):
        return filename[: -len(suffix)]
    return filename


def _passport_file_path(passport: Passport) -> Path:
    if not passport.file:
        raise Http404("Файл не привязан")

    file_path = Path(passport.file.path)
    if not file_path.exists():
        raise Http404("Файл не найден на диске")
    return file_path


def _passport_file_response(passport: Passport) -> FileResponse:
    file_path = _passport_file_path(passport)
    content_type, _ = mimetypes.guess_type(str(file_path))
    content_type = content_type or "application/octet-stream"

    return FileResponse(
        open(file_path, "rb"),
        as_attachment=False,
        content_type=content_type,
        filename=_passport_download_filename(passport, file_path),
    )


def _passport_browser_response(request, passport: Passport, raw_url: str):
    file_path = _passport_file_path(passport)
    if (passport.file_ext or _ext(file_path.name)).lower() != "pdf":
        return _passport_file_response(passport)

    return render(
        request,
        "passports_app/passport_file_viewer.html",
        {
            "title": _passport_browser_title(passport, file_path),
            "raw_url": raw_url,
        },
    )


class PassportsListView(PermissionRequiredMixin, View):
    permission_required = "passports_app.view_passport"
    raise_exception = True

    def get(self, request):
        needs_review_filter_on = request.GET.get("needs_review") == "1"

        qs = (
            Passport.objects.select_related("material")
            .only(
                "id",
                "document_name",
                "document_number",
                "document_date",
                "needs_review",
                "material__name",
            )
            .order_by("-created_at")
        )

        needs_review_count = Passport.objects.filter(needs_review=True).count()
        if needs_review_filter_on:
            qs = qs.filter(needs_review=True)

        return render(
            request,
            "passports_app/passports_list.html",
            {
                "passports": qs,
                "needs_review_filter_on": needs_review_filter_on,
                "needs_review_count": needs_review_count,
            },
        )


class PassportUploadView(PermissionRequiredMixin, View):
    permission_required = "passports_app.add_passport"
    raise_exception = True

    def get(self, request):
        form = PassportUploadForm()
        materials = list(Material.objects.order_by("name").values_list("name", flat=True))
        return render(request, "passports_app/passport_upload.html", {"form": form, "materials": materials})

    def post(self, request):
        form = PassportUploadForm(request.POST, request.FILES)
        materials = list(Material.objects.order_by("name").values_list("name", flat=True))

        if not form.is_valid():
            return render(request, "passports_app/passport_upload.html", {"form": form, "materials": materials})

        uploaded = form.cleaned_data["file"]
        ext = _ext(uploaded.name)

        # ===== ZIP: массовый импорт, остаёмся на /passports/add/ и показываем отчёт =====
        if ext == "zip":
            try:
                stats, results = import_passports_from_zip(archive_file=uploaded, user=request.user)
            except ValueError as e:
                form.add_error("file", str(e))
                return render(request, "passports_app/passport_upload.html", {"form": form, "materials": materials})

            messages.success(
                request,
                f"Импорт завершён. Успешно: {stats['imported']}, требуют проверки: {stats['needs_review']}, "
                f"пропущено: {stats['skipped']}, ошибок: {stats['errors']}.",
            )

            new_form = PassportUploadForm()
            return render(
                request,
                "passports_app/passport_upload.html",
                {
                    "form": new_form,
                    "materials": materials,
                    "zip_stats": stats,
                    "zip_results": results,
                },
            )

        # ===== Одиночный файл =====
        try:
            passport = import_single_passport_file(
                uploaded_file=uploaded,
                user=request.user,
                material_name=form.cleaned_data.get("material"),
                document_name=form.cleaned_data.get("document_name"),
                document_number=form.cleaned_data.get("document_number"),
                document_date=form.cleaned_data.get("document_date"),
            )
        except ValueError as e:
            form.add_error("file", str(e))
            return render(request, "passports_app/passport_upload.html", {"form": form, "materials": materials})

        if passport.needs_review:
            messages.warning(request, "Паспорт сохранён, но требует проверки (данные не полностью распознаны).")
        else:
            messages.success(request, "Паспорт успешно сохранён.")

        action = (form.cleaned_data.get("action") or "").strip()
        if action == "save_add_more":
            return redirect(reverse("passports:passports_add"))

        return redirect(reverse("passports:passport_detail", kwargs={"pk": passport.pk}))


class PassportDetailView(PermissionRequiredMixin, View):
    """
    Детали + редактирование.
    - Просмотр: passports_app.view_passport
    - Сохранение: passports_app.change_passport (проверяем вручную в post)
    """
    permission_required = "passports_app.view_passport"
    raise_exception = True

    def get(self, request, pk: int):
        passport = get_object_or_404(Passport.objects.select_related("material", "uploaded_by"), pk=pk)

        # (is_pdf) определяем, можно ли показывать iframe preview
        ext = (passport.file_ext or "").lower()
        if not ext and passport.file:
            ext = _ext(passport.file.name)
        is_pdf = ext == "pdf"

        form = PassportUpdateForm(instance=passport)
        materials = list(Material.objects.order_by("name").values_list("name", flat=True))

        return render(
            request,
            "passports_app/passport_detail.html",
            {"passport": passport, "form": form, "materials": materials, "is_pdf": is_pdf},
        )

    def post(self, request, pk: int):
        if not request.user.has_perm("passports_app.change_passport"):
            return HttpResponseForbidden("Нет прав на редактирование паспорта.")

        passport = get_object_or_404(Passport.objects.select_related("material", "uploaded_by"), pk=pk)
        old_file_path = None
        try:
            if passport.file:
                old_file_path = Path(passport.file.path)
        except Exception:
            old_file_path = None

        form = PassportUpdateForm(request.POST, request.FILES, instance=passport)
        materials = list(Material.objects.order_by("name").values_list("name", flat=True))

        ext = (passport.file_ext or "").lower()
        if not ext and passport.file:
            ext = _ext(passport.file.name)
        is_pdf = ext == "pdf"

        if not form.is_valid():
            return render(
                request,
                "passports_app/passport_detail.html",
                {"passport": passport, "form": form, "materials": materials, "is_pdf": is_pdf},
            )

        saved_passport = form.save()

        new_file_path = None
        try:
            if saved_passport.file:
                new_file_path = Path(saved_passport.file.path)
        except Exception:
            new_file_path = None

        if old_file_path and new_file_path and old_file_path != new_file_path and old_file_path.exists():
            try:
                old_file_path.unlink()
            except Exception:
                pass

        if form.cleaned_data.get("replacement_file"):
            messages.success(request, "Данные паспорта и файл сохранены.")
        else:
            messages.success(request, "Данные паспорта сохранены.")
        return redirect(reverse("passports:passport_detail", kwargs={"pk": passport.pk}))

@method_decorator(xframe_options_sameorigin, name="dispatch")
class PassportOpenView(PermissionRequiredMixin, View):
    permission_required = "passports_app.view_passport"
    raise_exception = True

    def get(self, request, pk: int):
        passport = get_object_or_404(Passport.objects.select_related("material"), pk=pk)
        if request.GET.get("raw") == "1":
            return _passport_file_response(passport)

        raw_url = f"{reverse('passports:passport_open', kwargs={'pk': passport.pk})}?raw=1"
        return _passport_browser_response(request, passport, raw_url)


class PassportShareLinkCreateView(PermissionRequiredMixin, View):
    permission_required = "passports_app.view_passport"
    raise_exception = True

    def post(self, request, pk: int):
        passport = get_object_or_404(Passport, pk=pk)
        if not passport.file:
            return JsonResponse({"error": "file_missing"}, status=400)

        ttl_hours = getattr(settings, "PASSPORT_SHARE_LINK_TTL_HOURS", 24)
        expires_at = timezone.now() + timedelta(hours=float(ttl_hours))
        link = PassportShareLink.objects.create(
            passport=passport,
            created_by=request.user,
            expires_at=expires_at,
        )
        url = request.build_absolute_uri(reverse("passports:passport_shared_open", kwargs={"token": link.token}))
        return JsonResponse({"url": url, "expires_at": link.expires_at.isoformat()})


@method_decorator(xframe_options_sameorigin, name="dispatch")
class PassportSharedOpenView(View):
    def get(self, request, token: str):
        link = get_object_or_404(
            PassportShareLink.objects.select_related("passport", "passport__material"),
            token=token,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        if request.GET.get("raw") == "1":
            PassportShareLink.objects.filter(pk=link.pk).update(
                access_count=F("access_count") + 1,
                last_accessed_at=timezone.now(),
            )
            return _passport_file_response(link.passport)

        raw_url = f"{reverse('passports:passport_shared_open', kwargs={'token': link.token})}?raw=1"
        return _passport_browser_response(request, link.passport, raw_url)


class PassportDeleteView(PermissionRequiredMixin, View):
    """
    Удаление паспорта по POST (из списка).
    кнопка -> form POST -> delete -> redirect на список.
    """
    permission_required = "passports_app.delete_passport"
    raise_exception = True

    def post(self, request, pk: int):
        passport = get_object_or_404(Passport, pk=pk)

        # (file_path) удалим файл вручную, чтобы не оставлять мусор на диске
        file_path = None
        try:
            if passport.file:
                file_path = Path(passport.file.path)
        except Exception:
            file_path = None

        name = passport.original_name or (passport.file.name if passport.file else f"паспорт #{passport.pk}")

        passport.delete()

        if file_path and file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                # не критично: запись удалена, файл мог быть занят/без прав
                pass

        messages.success(request, f"Паспорт удалён: {name}")
        return redirect(reverse("passports:passports_list"))





def is_superuser(user):
    return user.is_authenticated and user.is_superuser


@require_POST
@user_passes_test(is_superuser)
@transaction.atomic
def delete_all_passports(request):
    """
    Удаляет ВСЕ паспорта из БД.
    transaction.atomic — чтобы либо удалилось всё, либо ничего.
    """
    Passport.objects.all().delete()
    return redirect("passports:passports_list")
