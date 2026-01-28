# acts_app/forms.py
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory

from acts_app.models import (
    Act,
    ActAttachment,
    ActMaterialItem,
    AttachmentType,
    MaterialKind,
)
from acts_app.services.passport_suggestions import PassportSuggester, PassportSuggestionConfig


def _bootstrapify(form: forms.Form):
    for name, field in form.fields.items():
        w = field.widget
        cls = w.attrs.get("class", "")

        if isinstance(w, (forms.Select, forms.SelectMultiple)):
            base = "form-select"
        elif isinstance(w, (forms.CheckboxInput,)):
            base = "form-check-input"
        elif isinstance(w, (forms.FileInput,)):
            base = "form-control"
        else:
            base = "form-control"

        w.attrs["class"] = (cls + " " + base).strip()

        if isinstance(w, forms.Textarea):
            w.attrs.setdefault("rows", 3)

        if isinstance(w, forms.NumberInput):
            w.attrs.setdefault("min", "1")


def get_suggested_passports_qs(project_id: int, limit: int = 200):
    return PassportSuggester(project_id=project_id, config=PassportSuggestionConfig(limit=limit)).queryset()


class ActForm(forms.ModelForm):
    class Meta:
        model = Act
        fields = (
            "project",
            "number",
            "act_date",
            "work_name",
            "work_start_date",
            "work_end_date",
            "work_norms_text",
            "allow_next_works_text",
            "extra_info_text",
            "copies_count",
            "status",
        )
        widgets = {
            "act_date": forms.DateInput(attrs={"type": "date"}),
            "work_start_date": forms.DateInput(attrs={"type": "date"}),
            "work_end_date": forms.DateInput(attrs={"type": "date"}),
            "work_norms_text": forms.Textarea(attrs={"rows": 3}),
            "allow_next_works_text": forms.Textarea(attrs={"rows": 3}),
            "extra_info_text": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("work_start_date")
        end = cleaned.get("work_end_date")
        if start and end and end < start:
            raise ValidationError("Дата окончания работ не может быть раньше даты начала.")
        return cleaned


class ActAttachmentForm(forms.ModelForm):
    class Meta:
        model = ActAttachment
        fields = ("type", "title", "doc_no", "doc_date", "sheets_count", "file")
        widgets = {"doc_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("sheets_count") in (None, ""):
            raise ValidationError("Укажи количество листов.")
        return cleaned


class ActMaterialItemForm(forms.ModelForm):
    passport = forms.ModelChoiceField(queryset=None, required=False, label="Паспорт (из БД)")

    class Meta:
        model = ActMaterialItem
        fields = (
            "position",
            "passport",
            "manual_name",
            "manual_doc_no",
            "manual_doc_date",
            "manual_issuer",
            "material_kind",
            "sheets_count",
            "volume_m3",
            "note",
        )
        widgets = {"manual_doc_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, project_id: Optional[int] = None, **kwargs):
        super().__init__(*args, **kwargs)

        if project_id:
            self.fields["passport"].queryset = get_suggested_passports_qs(project_id)
        else:
            from passports_app.models import Passport  # noqa
            self.fields["passport"].queryset = Passport.objects.all().order_by("-id")

        _bootstrapify(self)

    def clean(self):
        cleaned = super().clean()
        passport = cleaned.get("passport")
        manual_name = (cleaned.get("manual_name") or "").strip()
        sheets = cleaned.get("sheets_count")
        kind = cleaned.get("material_kind")
        vol = cleaned.get("volume_m3")

        if not passport and not manual_name:
            raise ValidationError("Выбери паспорт из БД или заполни наименование вручную.")

        if sheets in (None, ""):
            raise ValidationError("Укажи количество листов для материала/паспорта.")

        if kind == MaterialKind.CONCRETE_MIX:
            if vol in (None, ""):
                raise ValidationError("Для бетонной смеси нужно указать V бетона, м3.")
            if isinstance(vol, Decimal) and vol <= 0:
                raise ValidationError("V бетона, м3 должен быть больше 0.")
        else:
            if vol not in (None, ""):
                raise ValidationError("Объём указывается только для бетонной смеси. Для сетки/прочего оставь пустым.")

        return cleaned


class BaseActMaterialFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        seen = set()
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
                continue
            p = form.cleaned_data.get("passport")
            if p:
                if p.pk in seen:
                    raise ValidationError("Один и тот же паспорт нельзя добавить в акт дважды.")
                seen.add(p.pk)


ActMaterialFormSet = inlineformset_factory(
    parent_model=Act,
    model=ActMaterialItem,
    form=ActMaterialItemForm,
    formset=BaseActMaterialFormSet,
    extra=1,
    can_delete=True,
)


class BaseActAttachmentFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        reg = 0
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
                continue
            if form.cleaned_data.get("type") == AttachmentType.MATERIALS_REGISTRY:
                reg += 1
        if reg > 1:
            raise ValidationError("Реестр материалов можно прикрепить только один (MATERIALS_REGISTRY).")


ActAttachmentFormSet = inlineformset_factory(
    parent_model=Act,
    model=ActAttachment,
    form=ActAttachmentForm,
    formset=BaseActAttachmentFormSet,
    extra=1,
    can_delete=True,
)
