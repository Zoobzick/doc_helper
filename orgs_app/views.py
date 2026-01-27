from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Q
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from .forms import OrganizationForm, PersonForm, PersonNRSForm
from .models import Organization, Person, PersonNRS


# ====== Organizations ======

class OrganizationListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Organization
    template_name = "orgs_app/organization_list.html"
    context_object_name = "items"
    paginate_by = 20
    permission_required = "orgs_app.view_organization"

    def get_queryset(self):
        qs = Organization.objects.order_by("short_name")

        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(short_name__icontains=q)
                | Q(full_name__icontains=q)
                | Q(inn__icontains=q)
                | Q(ogrn__icontains=q)
            )

        is_active = (self.request.GET.get("is_active") or "").strip()
        if is_active in {"0", "1"}:
            qs = qs.filter(is_active=(is_active == "1"))

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        ctx["is_active"] = (self.request.GET.get("is_active") or "").strip()
        return ctx


class OrganizationDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Organization
    template_name = "orgs_app/organization_detail.html"
    context_object_name = "item"
    permission_required = "orgs_app.view_organization"

    def get_object(self, queryset=None):
        return Organization.objects.get(uuid=self.kwargs["uuid"])


class OrganizationCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Organization
    form_class = OrganizationForm
    template_name = "orgs_app/organization_form.html"
    permission_required = "orgs_app.add_organization"

    def get_success_url(self):
        return reverse_lazy("orgs_app:organization_detail", kwargs={"uuid": self.object.uuid})


class OrganizationUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Organization
    form_class = OrganizationForm
    template_name = "orgs_app/organization_form.html"
    permission_required = "orgs_app.change_organization"

    def get_object(self, queryset=None):
        return Organization.objects.get(uuid=self.kwargs["uuid"])

    def get_success_url(self):
        return reverse_lazy("orgs_app:organization_detail", kwargs={"uuid": self.object.uuid})


class OrganizationDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = Organization
    template_name = "orgs_app/confirm_delete.html"
    permission_required = "orgs_app.delete_organization"
    success_url = reverse_lazy("orgs_app:organization_list")

    def get_object(self, queryset=None):
        return Organization.objects.get(uuid=self.kwargs["uuid"])


# ====== Persons ======

class PersonListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Person
    template_name = "orgs_app/person_list.html"
    context_object_name = "items"
    paginate_by = 20
    permission_required = "orgs_app.view_person"

    def get_queryset(self):
        qs = Person.objects.order_by("last_name", "first_name", "middle_name")

        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(last_name__icontains=q)
                | Q(first_name__icontains=q)
                | Q(middle_name__icontains=q)
            )

        is_active = (self.request.GET.get("is_active") or "").strip()
        if is_active in {"0", "1"}:
            qs = qs.filter(is_active=(is_active == "1"))

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        ctx["is_active"] = (self.request.GET.get("is_active") or "").strip()
        return ctx


class PersonDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Person
    template_name = "orgs_app/person_detail.html"
    context_object_name = "item"
    permission_required = "orgs_app.view_person"

    def get_object(self, queryset=None):
        return (
            Person.objects
            .prefetch_related("nrs_records")
            .get(uuid=self.kwargs["uuid"])
        )


class PersonCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Person
    form_class = PersonForm
    template_name = "orgs_app/person_form.html"
    permission_required = "orgs_app.add_person"

    def get_success_url(self):
        return reverse_lazy("orgs_app:person_detail", kwargs={"uuid": self.object.uuid})


class PersonUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Person
    form_class = PersonForm
    template_name = "orgs_app/person_form.html"
    permission_required = "orgs_app.change_person"

    def get_object(self, queryset=None):
        return Person.objects.get(uuid=self.kwargs["uuid"])

    def get_success_url(self):
        return reverse_lazy("orgs_app:person_detail", kwargs={"uuid": self.object.uuid})


class PersonDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = Person
    template_name = "orgs_app/confirm_delete.html"
    permission_required = "orgs_app.delete_person"
    success_url = reverse_lazy("orgs_app:person_list")

    def get_object(self, queryset=None):
        return Person.objects.get(uuid=self.kwargs["uuid"])


# ====== Person NRS ======

class PersonNRSListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = PersonNRS
    template_name = "orgs_app/personnrs_list.html"
    context_object_name = "items"
    paginate_by = 20
    permission_required = "orgs_app.view_personnrs"

    def get_queryset(self):
        qs = (
            PersonNRS.objects
            .select_related("person")
            .order_by("person__last_name", "person__first_name", "-valid_from")
        )

        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(nrs_id__icontains=q)
                | Q(person__last_name__icontains=q)
                | Q(person__first_name__icontains=q)
                | Q(person__middle_name__icontains=q)
            )

        is_active = (self.request.GET.get("is_active") or "").strip()
        if is_active in {"0", "1"}:
            qs = qs.filter(is_active=(is_active == "1"))

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        ctx["is_active"] = (self.request.GET.get("is_active") or "").strip()
        return ctx


class PersonNRSDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = PersonNRS
    template_name = "orgs_app/personnrs_detail.html"
    context_object_name = "item"
    permission_required = "orgs_app.view_personnrs"

    def get_object(self, queryset=None):
        return PersonNRS.objects.select_related("person").get(uuid=self.kwargs["uuid"])


class PersonNRSCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = PersonNRS
    form_class = PersonNRSForm
    template_name = "orgs_app/personnrs_form.html"
    permission_required = "orgs_app.add_personnrs"

    def get_initial(self):
        initial = super().get_initial()
        person_uuid = (self.request.GET.get("person") or "").strip()
        if person_uuid:
            try:
                initial["person"] = Person.objects.get(uuid=person_uuid)
            except Person.DoesNotExist:
                pass
        return initial

    def get_success_url(self):
        return reverse_lazy("orgs_app:personnrs_detail", kwargs={"uuid": self.object.uuid})


class PersonNRSUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = PersonNRS
    form_class = PersonNRSForm
    template_name = "orgs_app/personnrs_form.html"
    permission_required = "orgs_app.change_personnrs"

    def get_object(self, queryset=None):
        return PersonNRS.objects.get(uuid=self.kwargs["uuid"])

    def get_success_url(self):
        return reverse_lazy("orgs_app:personnrs_detail", kwargs={"uuid": self.object.uuid})


class PersonNRSDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = PersonNRS
    template_name = "orgs_app/confirm_delete.html"
    permission_required = "orgs_app.delete_personnrs"
    success_url = reverse_lazy("orgs_app:personnrs_list")

    def get_object(self, queryset=None):
        return PersonNRS.objects.get(uuid=self.kwargs["uuid"])
