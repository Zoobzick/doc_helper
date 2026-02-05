from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from .forms import OrganizationForm, PersonForm, PersonNRSForm, OrganizationSroMembershipFormSet
from .models import Organization, Person, PersonNRS, OrganizationSroMembership, SroKind


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


def _ensure_two_sro_rows(org: Organization) -> None:
    """
    Гарантируем, что у организации всегда есть 2 строки СРО:
    - BUILD (строительство)
    - DESIGN (проектирование)
    """
    existing = set(org.sro_memberships.values_list("kind", flat=True))
    to_create: list[OrganizationSroMembership] = []

    if SroKind.BUILD not in existing:
        to_create.append(OrganizationSroMembership(organization=org, kind=SroKind.BUILD))
    if SroKind.DESIGN not in existing:
        to_create.append(OrganizationSroMembership(organization=org, kind=SroKind.DESIGN))

    if to_create:
        OrganizationSroMembership.objects.bulk_create(to_create)


class OrganizationCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Organization
    form_class = OrganizationForm
    template_name = "orgs_app/organization_form.html"
    permission_required = "orgs_app.add_organization"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # На create показывать formset можно, но это не критично для твоего кейса.
        # Главное — update существующих организаций.
        if self.request.method == "POST":
            ctx["sro_formset"] = OrganizationSroMembershipFormSet(self.request.POST, instance=self.object)
        else:
            ctx["sro_formset"] = None

        return ctx

    @transaction.atomic
    def form_valid(self, form):
        response = super().form_valid(form)

        # После создания — гарантируем 2 строки СРО (пусть будут пустые)
        _ensure_two_sro_rows(self.object)

        # Если на create UI formset не используем — сохранять нечего.
        # Но если позже включишь — тут можно будет сохранить POST formset.
        return response

    def get_success_url(self):
        return reverse_lazy("orgs_app:organization_detail", kwargs={"uuid": self.object.uuid})


class OrganizationUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Organization
    form_class = OrganizationForm
    template_name = "orgs_app/organization_form.html"
    permission_required = "orgs_app.change_organization"

    def get_object(self, queryset=None):
        return Organization.objects.get(uuid=self.kwargs["uuid"])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # ✅ ВОТ ЭТО ключевое: для старых организаций создаём 2 строки СРО автоматически
        _ensure_two_sro_rows(self.object)

        if self.request.method == "POST":
            ctx["sro_formset"] = OrganizationSroMembershipFormSet(self.request.POST, instance=self.object)
        else:
            ctx["sro_formset"] = OrganizationSroMembershipFormSet(instance=self.object)

        return ctx

    @transaction.atomic
    def form_valid(self, form):
        ctx = self.get_context_data()
        sro_formset = ctx["sro_formset"]

        if sro_formset is None:
            return super().form_valid(form)

        if not sro_formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, sro_formset=sro_formset))

        response = super().form_valid(form)
        sro_formset.instance = self.object
        sro_formset.save()
        return response

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
