# directive_app/views.py

import re

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, UpdateView

from .forms import DirectiveForm, AuthorizationForm
from .models import Directive, Authorization


class DirectiveListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Directive
    template_name = "directive_app/directive_list.html"
    context_object_name = "directives"

    # стандартное право Django на просмотр модели Directive
    permission_required = "directive_app.view_directive"
    raise_exception = True

    paginate_by = None

    def get_queryset(self):
        return (
            Directive.objects
            .select_related("issuer_organization")
            .order_by("-date", "number")
        )


class DirectiveDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Directive
    template_name = "directive_app/directive_detail.html"
    context_object_name = "directive"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"

    permission_required = "directive_app.view_directive"
    raise_exception = True

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


class DirectiveCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Directive
    form_class = DirectiveForm
    template_name = "directive_app/directive_upload.html"

    permission_required = "directive_app.add_directive"
    raise_exception = True

    def form_valid(self, form):
        obj = form.save()
        messages.success(self.request, "Документ-основание создан.")
        return redirect("directive_app:directive_detail", uuid=obj.uuid)


class DirectiveUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Directive
    form_class = DirectiveForm
    template_name = "directive_app/directive_upload.html"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"

    permission_required = "directive_app.change_directive"
    raise_exception = True

    def form_valid(self, form):
        obj = form.save()
        messages.success(self.request, "Документ-основание обновлён.")
        return redirect("directive_app:directive_detail", uuid=obj.uuid)


class AuthorizationCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Authorization
    form_class = AuthorizationForm
    template_name = "directive_app/authorization_form.html"

    # добавление полномочия
    permission_required = "directive_app.add_authorization"
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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["directive"] = self.directive
        return ctx


class AuthorizationUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Authorization
    form_class = AuthorizationForm
    template_name = "directive_app/authorization_form.html"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"

    permission_required = "directive_app.change_authorization"
    raise_exception = True

    def form_valid(self, form):
        obj = form.save()
        messages.success(self.request, "Полномочие обновлено.")
        return redirect("directive_app:directive_detail", uuid=obj.directive.uuid)


class DirectiveOpenView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "directive_app.view_directive"
    raise_exception = True

    def get(self, request, uuid):
        directive = get_object_or_404(Directive, uuid=uuid)
        if not directive.pdf_file:
            raise Http404("Файл не прикреплён.")
        return FileResponse(directive.pdf_file.open("rb"), content_type="application/pdf")


class DirectiveDownloadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "directive_app.view_directive"
    raise_exception = True

    def get(self, request, uuid):
        directive = get_object_or_404(Directive, uuid=uuid)
        if not directive.pdf_file:
            raise Http404("Файл не прикреплён.")

        org = directive.issuer_organization.short_name if directive.issuer_organization else ""
        date_str = directive.date.strftime("%d.%m.%Y")

        def safe(s: str) -> str:
            return re.sub(r"[\\/:*?\"<>|]+", "_", s).strip()

        filename = safe(f"№{directive.number} от {date_str} ({org}).pdf")

        resp = FileResponse(directive.pdf_file.open("rb"), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp


class DirectiveDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "directive_app.delete_directive"
    raise_exception = True

    def post(self, request, uuid):
        directive = get_object_or_404(Directive, uuid=uuid)

        if directive.pdf_file:
            directive.pdf_file.delete(save=False)

        directive.delete()

        messages.success(request, "Приказ удалён.")
        return redirect("directive_app:directive_list")
