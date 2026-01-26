from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin, LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import ListView, DetailView, CreateView, UpdateView

from .forms import DirectiveForm, AuthorizationForm
from .models import Directive, Authorization, DirectiveType

import re
from django.http import FileResponse, Http404
from django.views import View



class DirectiveListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Directive
    template_name = "directive_app/directive_list.html"
    context_object_name = "directives"

    # Права на просмотр реестра приказов
    permission_required = "directive_app.view_directives_page"
    raise_exception = True

    # DataTables сам делает поиск/сортировку/страницы на фронте
    paginate_by = None

    def get_queryset(self):
        return (
            Directive.objects
            .select_related("issuer_organization")
            .order_by("-date", "number")
        )


class DirectiveDetailView(DetailView):
    model = Directive
    template_name = "directive_app/directive_detail.html"
    context_object_name = "directive"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"

    def get_queryset(self):
        return Directive.objects.select_related("issuer_organization")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["authorizations"] = (
            Authorization.objects
            .select_related("organization", "person", "directive")
            .filter(directive=self.object)
            .order_by("organization__short_name", "role", "person__last_name")
        )
        return ctx


class DirectiveCreateView(CreateView):
    model = Directive
    form_class = DirectiveForm
    template_name = "directive_app/directive_upload.html"

    def form_valid(self, form):
        obj = form.save()
        messages.success(self.request, "Документ-основание создан.")
        return redirect("directive_app:directive_detail", uuid=obj.uuid)


class DirectiveUpdateView(UpdateView):
    model = Directive
    form_class = DirectiveForm
    template_name = "directive_app/directive_upload.html"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"

    def form_valid(self, form):
        obj = form.save()
        messages.success(self.request, "Документ-основание обновлён.")
        return redirect("directive_app:directive_detail", uuid=obj.uuid)


class AuthorizationCreateView(CreateView):
    model = Authorization
    form_class = AuthorizationForm
    template_name = "directive_app/authorization_form.html"
    permission_required = "directive_app.create_directive_page"
    raise_exception = True

    def dispatch(self, request, *args, **kwargs):
        self.directive = get_object_or_404(Directive, uuid=kwargs["directive_uuid"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        initial["organization"] = self.directive.issuer_organization
        initial["valid_from"] = self.directive.date
        return initial

    def form_valid(self, form):
        obj = form.save(commit=False)
        obj.directive = self.directive
        obj.save()
        messages.success(self.request, "Полномочие добавлено.")
        return redirect("directive_app:directive_detail", uuid=self.directive.uuid)

    def form_invalid(self, form):
        # важно: при ошибке формы шаблон всё равно должен знать directive
        return super().form_invalid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["directive"] = self.directive
        return ctx


class AuthorizationUpdateView(UpdateView):
    model = Authorization
    form_class = AuthorizationForm
    template_name = "directive_app/authorization_form.html"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"

    def form_valid(self, form):
        obj = form.save()
        messages.success(self.request, "Полномочие обновлено.")
        return redirect("directive_app:directive_detail", uuid=obj.directive.uuid)

class DirectiveOpenView(PermissionRequiredMixin, View):
    permission_required = "directive_app.view_directives_page"
    raise_exception = True

    def get(self, request, uuid):
        directive = get_object_or_404(Directive, uuid=uuid)
        if not directive.pdf_file:
            raise Http404("Файл не прикреплён.")
        # inline (откроется в браузере)
        return FileResponse(directive.pdf_file.open("rb"), content_type="application/pdf")


class DirectiveDownloadView(PermissionRequiredMixin, View):
    permission_required = "directive_app.view_directives_page"
    raise_exception = True

    def get(self, request, uuid):
        directive = get_object_or_404(Directive, uuid=uuid)
        if not directive.pdf_file:
            raise Http404("Файл не прикреплён.")

        org = directive.issuer_organization.short_name if directive.issuer_organization else ""
        date_str = directive.date.strftime("%d.%m.%Y")

        # безопасное имя файла (убираем запрещённые символы)
        def safe(s: str) -> str:
            s = re.sub(r"[\\/:*?\"<>|]+", "_", s)
            return s.strip()

        filename = safe(f"№{directive.number} от {date_str} ({org}).pdf")

        resp = FileResponse(directive.pdf_file.open("rb"), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp


class DirectiveDeleteView(PermissionRequiredMixin, View):
    permission_required = "directive_app.create_directive_page"
    raise_exception = True

    def post(self, request, uuid):
        directive = get_object_or_404(Directive, uuid=uuid)

        # 1) удалить файл из хранилища
        if directive.pdf_file:
            directive.pdf_file.delete(save=False)

        # 2) удалить запись (Authorization удалятся каскадом, т.к. FK на Directive)
        directive.delete()

        messages.success(request, "Приказ удалён.")
        return redirect("directive_app:directive_list")

