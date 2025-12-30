# documents_app/views.py
from __future__ import annotations

from django.conf import settings
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin

from projects_app.models import Project
from .forms import BoxLabelForm
from .services.box_label_docx import render_box_label_docx


DEFAULT_DSM = 'ГУП "Московский метрополитен"'
DEFAULT_MIP = 'АО "Мосинжпроект"'
DEFAULT_SMU = 'ООО "СМУ-12 Мосметростроя"'

MAX_LINES = 50


class BoxLabelPageView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.view_box_label_page"
    raise_exception = True  # 403 вместо редиректа

    def get(self, request):
        return render(request, "documents_app/box_label_page.html", {
            "default_dsm": DEFAULT_DSM,
            "default_mip": DEFAULT_MIP,
            "default_smu": DEFAULT_SMU,
        })


class BoxLabelProjectSearchView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.view_box_label_page"
    raise_exception = True

    def get(self, request):
        q = (request.GET.get("q") or "").strip()

        qs = Project.objects.all().select_related("line", "plot", "designer", "section")
        if q:
            qs = qs.filter(
                Q(designer__code__icontains=q) |
                Q(line__code__icontains=q) |
                Q(plot__code__icontains=q) |
                Q(section__code__icontains=q) |
                Q(construction__icontains=q)
            )

        qs = qs.order_by("-id")[:30]
        return JsonResponse({
            "results": [{
                "id": p.id,
                "full_code": p.full_code,
                "construction": p.construction,
            } for p in qs]
        })


class BoxLabelGenerateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.view_box_label_page"
    raise_exception = True

    def post(self, request):
        form = BoxLabelForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"error": form.errors.get_json_data()}, status=400)

        dsm = form.cleaned_data.get("DSM") or DEFAULT_DSM
        mip = form.cleaned_data.get("MIP") or DEFAULT_MIP
        smu = form.cleaned_data.get("SMU") or DEFAULT_SMU

        exec_ids: list[int] = form.cleaned_data["exec_ids"]
        work_ids: list[int] = form.cleaned_data["work_ids"]
        all_ids = list({*exec_ids, *work_ids})

        projects = {
            p.id: p
            for p in Project.objects.filter(id__in=all_ids).select_related("line", "plot")
        }

        first_project = projects.get(all_ids[0])
        if not first_project:
            return JsonResponse({"error": "Проекты не найдены"}, status=400)

        object_ = first_project.line.full_name
        plot_ = first_project.plot.code

        kits = []
        for pid in exec_ids:
            p = projects.get(pid)
            if p:
                kits.append(f"Комплект исполнительной документации: {p.full_code} (папка №1, 2-шт.)")

        works = []
        for pid in work_ids:
            p = projects.get(pid)
            if p:
                works.append(f"Комплект рабочей документации: {p.full_code} (папка №1, 2-шт.)")

        ctx = {"DSM": dsm, "MIP": mip, "SMU": smu, "object": object_, "plot": plot_}

        for i in range(1, MAX_LINES + 1):
            ctx[f"kit_{i}"] = kits[i - 1] if i <= len(kits) else ""
            ctx[f"work_{i}"] = works[i - 1] if i <= len(works) else ""

        template_path = str(settings.DOCX_TEMPLATES_DIR / "шаблон_наклейка_коробка.docx")
        docx_buf = render_box_label_docx(template_path, ctx)

        resp = HttpResponse(
            docx_buf.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        resp["Content-Disposition"] = 'attachment; filename="наклейка_коробка.docx"'
        return resp
