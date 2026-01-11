from __future__ import annotations

from django.conf import settings
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin

from projects_app.models import Project, Stage
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
        stages = Stage.objects.filter(is_active=True).order_by("code")

        return render(request, "documents_app/box_label_page.html", {
            "default_dsm": DEFAULT_DSM,
            "default_mip": DEFAULT_MIP,
            "default_smu": DEFAULT_SMU,
            "stages": stages,  # (stages) список этапов для select
        })


class BoxLabelProjectSearchView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.view_box_label_page"
    raise_exception = True

    def get(self, request):
        q = (request.GET.get("q") or "").strip()

        qs = Project.objects.all().select_related("line", "plot", "designer", "section")
        if q:
            # ВАЖНО: добавили full_code__icontains=q — теперь "КЖ8" найдёт проект
            qs = qs.filter(
                Q(full_code__icontains=q) |                       # (full_code) основной поиск по шифру
                Q(designer__code__icontains=q) |
                Q(line__code__icontains=q) |
                Q(plot__code__icontains=q) |
                Q(plot__full_name__icontains=q) |                 # (plot.full_name) чтобы искать по названию участка
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
        stage_id: int | None = form.cleaned_data.get("stage_id")

        # (all_ids) — уникальные выбранные проекты (и ИД, и РД)
        all_ids = list({*exec_ids, *work_ids})

        projects = {
            p.id: p
            for p in Project.objects.filter(id__in=all_ids).select_related("line", "plot", "stage")
        }

        first_project = projects.get(all_ids[0])
        if not first_project:
            return JsonResponse({"error": "Проекты не найдены"}, status=400)

        # (object_) — объект берём из линии первого проекта (как было)
        object_ = first_project.line.full_name if first_project.line else ""

        # ✅ (plot_) — теперь full_name участка, а не code
        plot_ = first_project.plot.full_name if first_project.plot else ""

        # ✅ (stage_) — этап выбирается пользователем; если не выбран — берём из первого проекта
        stage_ = ""
        if stage_id:
            stage_ = get_object_or_404(Stage, pk=stage_id).full_name
        elif first_project.stage:
            stage_ = first_project.stage.full_name

        kits: list[str] = []
        for pid in exec_ids:
            p = projects.get(pid)
            if p:
                kits.append(f"Комплект исполнительной документации: {p.full_code} (папка №1, 2-шт.)")

        works: list[str] = []
        for pid in work_ids:
            p = projects.get(pid)
            if p:
                works.append(f"Комплект рабочей документации: {p.full_code} (папка №1, 2-шт.)")

        # (ctx) — ключи должны совпадать с {{...}} в docx
        ctx = {
            "DSM": dsm,
            "MIP": mip,
            "SMU": smu,
            "object": object_,
            "plot": plot_,
            "stage": stage_,
        }

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
