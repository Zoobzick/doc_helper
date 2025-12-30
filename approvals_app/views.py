import os
import re

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import urlencode
from django.views import View
from django.views.generic import ListView

from projects_app.models import Project
from .forms import ApprovalForm
from .models import Approval

TAIL_RE = re.compile(r"^([А-ЯA-Z]{2,3})(\d+)$")  # КЖ39, АР12, ОС4


# ---------- PERMISSIONS MIXINS ----------

class DonePageAccessMixin(LoginRequiredMixin, PermissionRequiredMixin):
    permission_required = "approvals_app.view_approvals_done_page"
    raise_exception = True
    login_url = "/login/"


class PendingPageAccessMixin(LoginRequiredMixin, PermissionRequiredMixin):
    permission_required = "approvals_app.view_approvals_pending_page"
    raise_exception = True
    login_url = "/login/"


class DeleteApprovalsAccessMixin(LoginRequiredMixin, PermissionRequiredMixin):
    permission_required = "approvals_app.delete_approvals"
    raise_exception = True
    login_url = "/login/"


class AddDoneAccessMixin(LoginRequiredMixin, PermissionRequiredMixin):
    permission_required = "approvals_app.add_approvals_done"
    raise_exception = True
    login_url = "/login/"


class AddPendingAccessMixin(LoginRequiredMixin, PermissionRequiredMixin):
    permission_required = "approvals_app.add_approvals_pending"
    raise_exception = True
    login_url = "/login/"


# ---------- LIST DONE ----------

class ApprovalDoneListView(DonePageAccessMixin, ListView):
    model = Approval
    template_name = "approvals_app/approvals_done_list.html"
    context_object_name = "approvals"
    paginate_by = 25

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .select_related("project")
            .filter(status=Approval.Status.DONE)
        )

        q = (self.request.GET.get("q") or "").strip()
        if not q:
            return qs

        q_up = q.upper()
        m = TAIL_RE.match(q_up)
        if m:
            # поиск типа "КЖ39" -> section + number
            return qs.filter(project__section__code=m.group(1), project__number=int(m.group(2)))

        # иначе — по описанию
        return qs.filter(description__icontains=q)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        form = ApprovalForm()

        # открываем модалку и prefill при редиректе с PENDING
        add = self.request.GET.get("add") == "1"
        pending_id = self.request.GET.get("pending_id")
        project_id = self.request.GET.get("project_id")
        description = self.request.GET.get("description", "")

        if add and self.request.user.has_perm("approvals_app.add_approvals_done"):
            ctx["open_modal"] = True

        if description:
            form.initial["description"] = description

        ctx["initial_project_id"] = project_id
        ctx["initial_project_text"] = ""
        if project_id:
            try:
                p = Project.objects.get(pk=int(project_id))
                ctx["initial_project_text"] = p.full_code
            except Exception:
                pass

        ctx["pending_id"] = pending_id
        ctx["form"] = form

        # чтобы в шаблоне можно было прятать кнопку "Добавить"
        ctx["can_add_done"] = self.request.user.has_perm("approvals_app.add_approvals_done")
        ctx["can_delete"] = self.request.user.has_perm("approvals_app.delete_approvals")

        return ctx

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        if not request.user.has_perm("approvals_app.add_approvals_done"):
            return redirect("approvals:done")

        form = ApprovalForm(request.POST, request.FILES)
        pending_id = request.POST.get("pending_id")

        if form.is_valid():
            obj = form.save(commit=False)
            obj.status = Approval.Status.DONE
            obj.save()

            # если пришли с pending -> удалить pending запись
            if pending_id:
                Approval.objects.filter(pk=pending_id, status=Approval.Status.PENDING).delete()

            messages.success(request, "Добавлено в «Все согласования»")
            return redirect("approvals:done")

        self.object_list = self.get_queryset()
        ctx = self.get_context_data()
        ctx["form"] = form
        ctx["open_modal"] = True
        ctx["pending_id"] = pending_id
        return self.render_to_response(ctx)


# ---------- LIST PENDING ----------

class ApprovalPendingListView(PendingPageAccessMixin, ListView):
    model = Approval
    template_name = "approvals_app/approvals_pending_list.html"
    context_object_name = "approvals"
    paginate_by = 25

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .select_related("project")
            .filter(status=Approval.Status.PENDING)
        )

        q = (self.request.GET.get("q") or "").strip()
        if not q:
            return qs

        q_up = q.upper()
        m = TAIL_RE.match(q_up)
        if m:
            return qs.filter(project__section__code=m.group(1), project__number=int(m.group(2)))

        return qs.filter(description__icontains=q)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = ApprovalForm()
        ctx["can_add_pending"] = self.request.user.has_perm("approvals_app.add_approvals_pending")
        ctx["can_mark_done"] = self.request.user.has_perm("approvals_app.view_approvals_done_page")
        ctx["can_delete"] = self.request.user.has_perm("approvals_app.delete_approvals")
        return ctx

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm("approvals_app.add_approvals_pending"):
            return redirect("approvals:pending")

        form = ApprovalForm(request.POST, request.FILES)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.status = Approval.Status.PENDING
            obj.save()
            messages.success(request, "Добавлено в «На согласовании»")
            return redirect("approvals:pending")

        self.object_list = self.get_queryset()
        ctx = self.get_context_data()
        ctx["form"] = form
        ctx["open_modal"] = True
        return self.render_to_response(ctx)


# ---------- MARK DONE (redirect to DONE with open modal) ----------
# ВАЖНО: доступ только у тех, кто имеет view DONE (обычно worker).
# mark12 таким образом НЕ сможет "провалиться" на done.

class ApprovalMarkDoneRedirectView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "approvals_app.view_approvals_done_page"
    raise_exception = True
    login_url = "/login/"

    def post(self, request, pk: int):
        pending = get_object_or_404(Approval, pk=pk, status=Approval.Status.PENDING)

        params = {"add": "1", "pending_id": str(pending.pk)}
        if pending.project_id:
            params["project_id"] = str(pending.project_id)
        if pending.description:
            params["description"] = pending.description

        url = reverse("approvals:done")
        return redirect(f"{url}?{urlencode(params)}")


# ---------- OPEN PDF ----------

class ApprovalOpenPdfView(PendingPageAccessMixin, View):
    def get(self, request, pk: int):
        approval = get_object_or_404(Approval, pk=pk)

        try:
            return FileResponse(open(approval.file.path, "rb"), content_type="application/pdf")
        except FileNotFoundError:
            raise Http404("Файл согласования не найден на диске")


# ---------- DELETE ----------

class ApprovalDeleteView(DeleteApprovalsAccessMixin, View):
    def post(self, request, pk: int):
        approval = get_object_or_404(Approval, pk=pk)

        if approval.file and os.path.isfile(approval.file.path):
            os.remove(approval.file.path)

        approval.delete()
        messages.success(request, "Согласование удалено")
        return redirect(request.META.get("HTTP_REFERER", reverse("approvals:pending")))


# ---------- PROJECT SEARCH (Select2) ----------
# Доступно тем, кто видит pending (mark12 тоже).

class ProjectSearchView(PendingPageAccessMixin, View):
    def get(self, request):
        q = (request.GET.get("q") or "").strip().upper()

        qs = Project.objects.select_related(
            "designer", "line", "design_stage", "stage", "plot", "section"
        )

        m = TAIL_RE.match(q)
        if m:
            qs = qs.filter(section__code=m.group(1), number=int(m.group(2)))
        else:
            qs = (
                    qs.filter(designer__code__icontains=q)
                    | qs.filter(line__code__icontains=q)
                    | qs.filter(stage__code__icontains=q)
                    | qs.filter(plot__code__icontains=q)
                    | qs.filter(section__code__icontains=q)
            )

        qs = qs.order_by("id")[:50]
        return JsonResponse({"results": [{"id": p.id, "text": p.full_code} for p in qs]})
