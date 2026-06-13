import re

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Length
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import urlencode
from django.views import View
from django.views.generic import ListView
from django.views.generic.edit import UpdateView

from projects_app.models import Project
from .forms import ApprovalForm
from .models import Approval


TAIL_RE = re.compile(r"^([А-ЯA-Z]{2,3})(\d+)$")  # КЖ39, АР12, ОС4 (хвост шифра)


def _delete_approval_file(approval: Approval) -> None:
    """
    approval (Approval) — объект согласования, у которого удаляем PDF.

    ВАЖНО: удаляем через FileField.delete() => через storage,
    а не через os.remove() и не через QuerySet.delete().
    """
    if approval.file and getattr(approval.file, "name", ""):
        approval.file.delete(save=False)


def _apply_search(qs, q: str):
    """
    qs (QuerySet[Approval]) — базовый queryset
    q (str) — строка из GET параметра q
    """
    q = (q or "").strip()
    if not q:
        return qs

    q_up = q.upper()

    cond = Q(description__icontains=q) | Q(construction__icontains=q)

    if TAIL_RE.match(q_up):
        cond |= Q(project__full_code__iendswith=q_up) | Q(project__full_code__icontains=q_up)
    else:
        cond |= Q(project__full_code__icontains=q)

    return qs.filter(cond)


# ---------- DONE LIST ----------

class ApprovalDoneListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "approvals_app.view_approvals_done_page"
    raise_exception = True
    login_url = "/login/"

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
        q = self.request.GET.get("q") or ""
        return _apply_search(qs, q)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        form = ApprovalForm()

        add = self.request.GET.get("add") == "1"
        pending_id = self.request.GET.get("pending_id")
        project_id = self.request.GET.get("project_id")
        description = self.request.GET.get("description", "")
        construction = self.request.GET.get("construction", "")

        if add and self.request.user.has_perm("approvals_app.add_approvals_done"):
            ctx["open_modal"] = True

        if description:
            form.initial["description"] = description
        if construction:
            form.initial["construction"] = construction

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

        ctx["can_add_done"] = self.request.user.has_perm("approvals_app.add_approvals_done")
        ctx["can_delete"] = self.request.user.has_perm("approvals_app.delete_approvals")
        ctx["can_edit"] = self.request.user.has_perm("approvals_app.change_approval")

        return ctx

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        if not request.user.has_perm("approvals_app.add_approvals_done"):
            return redirect("approvals:done")

        pending_id = (request.POST.get("pending_id") or "").strip()
        pending_obj = None
        old_file_name = ""

        if pending_id:
            pending_obj = (
                Approval.objects
                .select_for_update()
                .filter(pk=pending_id, status=Approval.Status.PENDING)
                .first()
            )
            if pending_obj and pending_obj.file and getattr(pending_obj.file, "name", ""):
                old_file_name = pending_obj.file.name

        form = ApprovalForm(request.POST, request.FILES, instance=pending_obj)

        if form.is_valid():
            done_obj = form.save(commit=False)
            done_obj.status = Approval.Status.DONE
            done_obj.save()

            if old_file_name and old_file_name != getattr(done_obj.file, "name", ""):
                try:
                    done_obj.file.storage.delete(old_file_name)
                except Exception:
                    pass

            messages.success(request, "Добавлено в «Все согласования»")
            return redirect("approvals:done")

        self.object_list = self.get_queryset()
        ctx = self.get_context_data()
        ctx["form"] = form
        ctx["open_modal"] = True
        ctx["pending_id"] = pending_id
        return self.render_to_response(ctx)


# ---------- PENDING LIST ----------

class ApprovalPendingListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "approvals_app.view_approvals_pending_page"
    raise_exception = True
    login_url = "/login/"

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
        q = self.request.GET.get("q") or ""
        return _apply_search(qs, q)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = ApprovalForm()
        ctx["can_add_pending"] = self.request.user.has_perm("approvals_app.add_approvals_pending")
        ctx["can_mark_done"] = self.request.user.has_perm("approvals_app.view_approvals_done_page")
        ctx["can_delete"] = self.request.user.has_perm("approvals_app.delete_approvals")
        ctx["can_edit"] = self.request.user.has_perm("approvals_app.change_approval")
        return ctx

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm("approvals_app.add_approvals_pending"):
            return redirect("approvals:pending")

        form = ApprovalForm(request.POST, request.FILES)
        if form.is_valid():
            pending_obj = form.save(commit=False)
            pending_obj.status = Approval.Status.PENDING
            pending_obj.save()
            messages.success(request, "Добавлено в «На согласовании»")
            return redirect("approvals:pending")

        self.object_list = self.get_queryset()
        ctx = self.get_context_data()
        ctx["form"] = form
        ctx["open_modal"] = True
        return self.render_to_response(ctx)


# ---------- MARK DONE (redirect to DONE with open modal) ----------

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
        if pending.construction:
            params["construction"] = pending.construction

        url = reverse("approvals:done")
        return redirect(f"{url}?{urlencode(params)}")


# ---------- OPEN PDF ----------

class ApprovalOpenPdfView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "approvals_app.view_approvals_pending_page"
    raise_exception = True
    login_url = "/login/"

    def get(self, request, pk: int):
        approval = get_object_or_404(Approval, pk=pk)
        try:
            approval.file.open("rb")
            return FileResponse(approval.file, content_type="application/pdf")
        except FileNotFoundError:
            raise Http404("Файл согласования не найден на диске")


# ---------- EDIT ----------

class ApprovalUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    Редактирование согласования:
    - project / construction / description
    - замена PDF файла (старый файл удаляем из storage)
    """
    permission_required = "approvals_app.change_approval"
    raise_exception = True
    login_url = "/login/"

    model = Approval
    form_class = ApprovalForm
    template_name = "approvals_app/approval_edit.html"
    context_object_name = "approval"

    def get_success_url(self):
        if self.object.status == Approval.Status.DONE:
            return reverse("approvals:done")
        return reverse("approvals:pending")

    def form_valid(self, form):
        # (old_file_name) — путь старого файла (если пользователь загрузил новый)
        old_file_name = ""
        if "file" in form.changed_data and self.object.file and getattr(self.object.file, "name", ""):
            old_file_name = self.object.file.name

        resp = super().form_valid(form)

        # Удаляем старый файл ПОСЛЕ успешного сохранения нового
        if old_file_name and old_file_name != getattr(self.object.file, "name", ""):
            try:
                self.object.file.storage.delete(old_file_name)
            except Exception:
                # не падаем из-за мусора в storage
                pass

        messages.success(self.request, "Согласование обновлено")
        return resp


# ---------- DELETE ----------

class ApprovalDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "approvals_app.delete_approvals"
    raise_exception = True
    login_url = "/login/"

    def post(self, request, pk: int):
        approval = get_object_or_404(Approval, pk=pk)

        _delete_approval_file(approval)
        approval.delete()

        messages.success(request, "Согласование удалено")
        return redirect(request.META.get("HTTP_REFERER", reverse("approvals:pending")))


# ---------- PROJECT SEARCH (Select2) ----------

class ProjectSearchView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "approvals_app.view_approvals_pending_page"
    raise_exception = True
    login_url = "/login/"

    def get(self, request):
        q_raw = (request.GET.get("q") or "").strip()
        q = q_raw

        qs = Project.objects.all().exclude(full_code__isnull=True)

        if q:
            qs = qs.filter(full_code__icontains=q)

        qs = qs.annotate(_len=Length("full_code")).order_by("_len", "full_code")[:50]

        return JsonResponse({"results": [{"id": p.id, "text": p.full_code} for p in qs]})
