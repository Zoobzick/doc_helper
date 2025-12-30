# directive_app/admin.py
from django.contrib import admin
from .models import Directive, SignerRoleConfig, SignerRole


@admin.register(SignerRoleConfig)
class SignerRoleConfigAdmin(admin.ModelAdmin):
    # (поля_в_списке)
    list_display = ("role", "source_paragraph", "is_enabled", "updated_at")
    # (редактирование_прямо_в_таблице)
    list_editable = ("source_paragraph", "is_enabled")
    # (фильтры_справа)
    list_filter = ("is_enabled", "role")
    # (поиск)
    search_fields = ("role",)
    # (сортировка)
    ordering = ("role",)

    # (действия) — быстро включить/выключить
    actions = ("enable_roles", "disable_roles")

    @admin.action(description="Включить выбранные роли")
    def enable_roles(self, request, queryset):
        queryset.update(is_enabled=True)

    @admin.action(description="Выключить выбранные роли")
    def disable_roles(self, request, queryset):
        queryset.update(is_enabled=False)


@admin.register(Directive)
class DirectiveAdmin(admin.ModelAdmin):
    # (поля_в_списке)
    list_display = (
        "number",
        "date",
        "effective_date",
        "effective_date_resolved_admin",
        "employee_full_name",
        "employee_position",
        "organization",
        "signer_role",
        "role_paragraph_admin",
        "is_active",
        "updated_at",
    )

    # (редактирование_прямо_в_таблице)
    list_editable = ("signer_role", "is_active")

    # (поиск)
    search_fields = ("number", "employee_full_name", "employee_position", "organization")

    # (фильтры_справа)
    list_filter = ("signer_role", "organization", "is_active", "date")

    # (сортировка)
    ordering = ("-date", "-number")

    # (действия_над_выбранными)
    actions = (
        "set_role_dsm_sk",
        "set_role_mip_rs",
        "set_role_mip_sk",
        "set_role_an",
        "set_role_smu",
        "set_role_mms_sk",
        "set_role_other",
    )

    @admin.display(description="Дата вступления (resolved)")
    def effective_date_resolved_admin(self, obj: Directive):
        return obj.effective_date_resolved

    @admin.display(description="Параграф роли")
    def role_paragraph_admin(self, obj: Directive):
        cfg = (
            SignerRoleConfig.objects
            .filter(role=obj.signer_role, is_enabled=True)
            .only("source_paragraph")
            .first()
        )
        return cfg.source_paragraph if (cfg and cfg.source_paragraph is not None) else "—"

    # ---------- helpers ----------
    def _set_role(self, queryset, role_value):
        queryset.update(signer_role=role_value)

    # ---------- actions ----------
    @admin.action(description="Роль: ДСМ СК")
    def set_role_dsm_sk(self, request, queryset):
        self._set_role(queryset, SignerRole.DSM_SK)

    @admin.action(description="Роль: МИП РС")
    def set_role_mip_rs(self, request, queryset):
        self._set_role(queryset, SignerRole.MIP_RS)

    @admin.action(description="Роль: МИП СК")
    def set_role_mip_sk(self, request, queryset):
        self._set_role(queryset, SignerRole.MIP_SK)

    @admin.action(description="Роль: АН")
    def set_role_an(self, request, queryset):
        self._set_role(queryset, SignerRole.AN)

    @admin.action(description="Роль: СМУ")
    def set_role_smu(self, request, queryset):
        self._set_role(queryset, SignerRole.SMU)

    @admin.action(description="Роль: ММС СК")
    def set_role_mms_sk(self, request, queryset):
        self._set_role(queryset, SignerRole.MMS_SK)

    @admin.action(description="Роль: ИНЫЕ")
    def set_role_other(self, request, queryset):
        self._set_role(queryset, SignerRole.OTHER)
