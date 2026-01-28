# acts_app/views.py
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView, DetailView

from acts_app.forms import ActForm, ActMaterialFormSet, ActAttachmentFormSet
from acts_app.models import Act, ActStatus
from acts_app.services.appendix_builder import AppendixBuilder, AppendixBuilderError


class ActListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "acts_app.view_act"
    model = Act
    template_name = "acts_app/act_list.html"
    context_object_name = "acts"
    paginate_by = 20

    def get_queryset(self):
        qs = (
            Act.objects
            .select_related("project")
            .order_by("-act_date", "number")
        )

        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(number__icontains=q)

        project_id = (self.request.GET.get("project") or "").strip()
        if project_id.isdigit():
            qs = qs.filter(project_id=int(project_id))

        status = (self.request.GET.get("status") or "").strip()
        if status in {ActStatus.DRAFT, ActStatus.FINAL}:
            qs = qs.filter(status=status)

        return qs


class ActDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "acts_app.view_act"
    model = Act
    template_name = "acts_app/act_detail.html"
    context_object_name = "act"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"

    def get_queryset(self):
        return (
            Act.objects
            .select_related("project")
            .prefetch_related("materials", "attachments", "appendix_lines")
        )


class ActCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.add_act"
    template_name = "acts_app/act_form.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        act_form = ActForm()
        material_fs = ActMaterialFormSet(prefix="mat")
        attach_fs = ActAttachmentFormSet(prefix="att")
        return render(
            request,
            self.template_name,
            {
                "form": act_form,
                "material_formset": material_fs,
                "attachment_formset": attach_fs,
                "mode": "create",
            },
        )

    @transaction.atomic
    def post(self, request: HttpRequest) -> HttpResponse:
        act_form = ActForm(request.POST)
        material_fs = ActMaterialFormSet(request.POST, prefix="mat")
        attach_fs = ActAttachmentFormSet(request.POST, request.FILES, prefix="att")

        if not (act_form.is_valid() and material_fs.is_valid() and attach_fs.is_valid()):
            return render(
                request,
                self.template_name,
                {
                    "form": act_form,
                    "material_formset": material_fs,
                    "attachment_formset": attach_fs,
                    "mode": "create",
                },
            )

        act = act_form.save()

        # важно: после появления act.project_id можем подмешать project_id в формы материалов на следующем редактировании
        material_fs.instance = act
        attach_fs.instance = act

        material_fs.save()
        attach_fs.save()

        # пересборка приложений (может упасть, например если нет схемы)
        try:
            AppendixBuilder(act).rebuild()
            messages.success(request, "Акт сохранён. Приложения пересобраны.")
        except AppendixBuilderError as e:
            messages.warning(request, f"Акт сохранён, но приложения не пересобраны: {e}")

        return redirect("acts_app:act_detail", uuid=str(act.uuid))


class ActUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"
    template_name = "acts_app/act_form.html"

    def get_object(self, uuid: str) -> Act:
        return get_object_or_404(Act, uuid=uuid)

    def get(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = self.get_object(uuid)

        act_form = ActForm(instance=act)

        # ВАЖНО: прокидываем project_id, чтобы в материалах паспорта были отсортированы “умно”
        material_fs = ActMaterialFormSet(
            instance=act,
            prefix="mat",
            form_kwargs={"project_id": act.project_id},
        )
        attach_fs = ActAttachmentFormSet(instance=act, prefix="att")

        return render(
            request,
            self.template_name,
            {
                "act": act,
                "form": act_form,
                "material_formset": material_fs,
                "attachment_formset": attach_fs,
                "mode": "update",
            },
        )

    @transaction.atomic
    def post(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = self.get_object(uuid)

        act_form = ActForm(request.POST, instance=act)
        material_fs = ActMaterialFormSet(
            request.POST,
            instance=act,
            prefix="mat",
            form_kwargs={"project_id": act.project_id},
        )
        attach_fs = ActAttachmentFormSet(
            request.POST,
            request.FILES,
            instance=act,
            prefix="att",
        )

        if not (act_form.is_valid() and material_fs.is_valid() and attach_fs.is_valid()):
            return render(
                request,
                self.template_name,
                {
                    "act": act,
                    "form": act_form,
                    "material_formset": material_fs,
                    "attachment_formset": attach_fs,
                    "mode": "update",
                },
            )

        act = act_form.save()
        material_fs.save()
        attach_fs.save()

        # кнопка "Сохранить без пересборки" — если нужно, можно добавить, но по умолчанию пересобираем всегда
        try:
            AppendixBuilder(act).rebuild()
            messages.success(request, "Изменения сохранены. Приложения пересобраны.")
        except AppendixBuilderError as e:
            messages.warning(request, f"Изменения сохранены, но приложения не пересобраны: {e}")

        return redirect("acts_app:act_detail", uuid=str(act.uuid))


class ActRebuildAppendixView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Отдельный эндпоинт на случай, если в интерфейсе будет кнопка
    “Пересобрать приложения” без сохранения других данных.
    """
    permission_required = "acts_app.change_act"

    @transaction.atomic
    def post(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = get_object_or_404(Act, uuid=uuid)
        try:
            AppendixBuilder(act).rebuild()
            messages.success(request, "Приложения пересобраны.")
        except AppendixBuilderError as e:
            messages.error(request, f"Не удалось пересобрать приложения: {e}")
        return redirect("acts_app:act_detail", uuid=str(act.uuid))
