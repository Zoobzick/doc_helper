from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from .forms import PassportUploadForm
from .models import Passport, Material
from .services import import_single_passport_file
from .services_archive import import_passports_from_zip


def _ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower().lstrip(".")


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

            # сбрасываем форму (чтобы повторно можно было грузить)
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

        # ===== Одиночный файл: старое поведение (поля + 2 кнопки) =====
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
    permission_required = "passports_app.view_passport"
    raise_exception = True

    def get(self, request, pk: int):
        passport = get_object_or_404(Passport.objects.select_related("material", "uploaded_by"), pk=pk)
        return render(request, "passports_app/passport_detail.html", {"passport": passport})


class PassportOpenView(PermissionRequiredMixin, View):
    permission_required = "passports_app.view_passport"
    raise_exception = True

    def get(self, request, pk: int):
        passport = get_object_or_404(Passport, pk=pk)

        if not passport.file:
            raise Http404("Файл не привязан")

        file_path = Path(passport.file.path)
        if not file_path.exists():
            raise Http404("Файл не найден на диске")

        content_type, _ = mimetypes.guess_type(str(file_path))
        content_type = content_type or "application/octet-stream"

        filename = passport.original_name or file_path.name
        resp = FileResponse(open(file_path, "rb"), content_type=content_type)
        resp["Content-Disposition"] = f'inline; filename="{filename}"'
        return resp
