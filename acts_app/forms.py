# acts_app/forms.py
from __future__ import annotations

from typing import Optional

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory

from acts_app.models import Act, ActAttachment, ActMaterialItem, AttachmentType


ISO_DATE_FORMAT = "%Y-%m-%d"


def iso_date_widget():
    # ВАЖНО: format задаёт value для type="date" как YYYY-MM-DD
    return forms.DateInput(attrs={"type": "date"}, format=ISO_DATE_FORMAT)


def force_iso_date_field(field: forms.Field):
    """
    Гарантирует, что:
    1) input type=date рендерит value как YYYY-MM-DD
    2) форма принимает YYYY-MM-DD при POST
    """
    if isinstance(field, forms.DateField):
        # widget.format используется при рендере value=""
        if isinstance(field.widget, forms.DateInput):
            field.widget.format = ISO_DATE_FORMAT
        # input_formats — чтобы принимать YYYY-MM-DD
        fmts = list(getattr(field, "input_formats", []) or [])
        if ISO_DATE_FORMAT not in fmts:
            field.input_formats = [ISO_DATE_FORMAT] + fmts


def _bootstrapify(form: forms.Form):
    for _, field in form.fields.items():
        w = field.widget
        cls = w.attrs.get("class", "")

        if isinstance(w, (forms.Select, forms.SelectMultiple)):
            base = "form-select"
        elif isinstance(w, (forms.CheckboxInput,)):
            base = "form-check-input"
        else:
            base = "form-control"

        w.attrs["class"] = (cls + " " + base).strip()

        if isinstance(w, forms.Textarea):
            w.attrs.setdefault("rows", 3)

        if isinstance(w, forms.NumberInput):
            w.attrs.setdefault("min", "1")


class ActProjectsForm(forms.Form):
    projects = forms.ModelMultipleChoiceField(
        queryset=None,
        required=True,
        label="Шифры проектов",
        widget=forms.MultipleHiddenInput,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from projects_app.models import Project  # noqa: WPS433
        self.fields["projects"].queryset = Project.objects.all().order_by("id")
        _bootstrapify(self)


class ActForm(forms.ModelForm):
    class Meta:
        model = Act
        fields = (
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
            "act_date": iso_date_widget(),
            "work_start_date": iso_date_widget(),
            "work_end_date": iso_date_widget(),
            "work_norms_text": forms.Textarea(attrs={"rows": 3}),
            "allow_next_works_text": forms.Textarea(attrs={"rows": 3}),
            "extra_info_text": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ВАЖНО: форсим ISO формат в полях дат
        for name in ("act_date", "work_start_date", "work_end_date"):
            if name in self.fields:
                force_iso_date_field(self.fields[name])

        _bootstrapify(self)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("work_start_date")
        end = cleaned.get("work_end_date")
        if start and end and end < start:
            raise ValidationError("Дата окончания работ не может быть раньше даты начала.")
        return cleaned


class ActMaterialItemForm(forms.ModelForm):
    """
    passport выбираем через модалку -> hidden id.
    queryset = Passport.objects.all() чтобы не было:
    "Вашего варианта нет среди допустимых значений".
    """
    passport = forms.ModelChoiceField(queryset=None, required=False, widget=forms.HiddenInput)

    class Meta:
        model = ActMaterialItem
        fields = (
            "passport",
            "manual_name",
            "manual_doc_no",
            "manual_doc_date",
            "sheets_count",
            "note",
        )
        widgets = {
            "manual_doc_date": iso_date_widget(),
        }

    def __init__(self, *args, project_id: Optional[int] = None, **kwargs):
        super().__init__(*args, **kwargs)

        from passports_app.models import Passport  # noqa: WPS433
        self.fields["passport"].queryset = Passport.objects.all().order_by("-id")

        # sheets_count дефолт 1 (чтобы не улетал NULL)
        self.fields["sheets_count"].required = False
        if self.initial.get("sheets_count") in (None, ""):
            self.initial["sheets_count"] = 1
        if getattr(self.instance, "sheets_count", None) in (None, ""):
            self.initial["sheets_count"] = 1

        # форсим ISO формат для manual_doc_date
        if "manual_doc_date" in self.fields:
            force_iso_date_field(self.fields["manual_doc_date"])

        _bootstrapify(self)

    def clean(self):
        cleaned = super().clean()

        passport = cleaned.get("passport")
        manual_name = (cleaned.get("manual_name") or "").strip()

        sheets = cleaned.get("sheets_count")
        if sheets in (None, ""):
            cleaned["sheets_count"] = 1
        else:
            try:
                sheets_int = int(sheets)
            except (TypeError, ValueError):
                raise ValidationError("Укажи количество листов числом.")
            if sheets_int < 1:
                raise ValidationError("Количество листов должно быть >= 1.")
            cleaned["sheets_count"] = sheets_int

        if not passport and not manual_name:
            raise ValidationError("Выбери паспорт из БД или заполни наименование материала вручную.")

        return cleaned


class BaseActMaterialFormSet(BaseInlineFormSet):
    """
    1) не даём дублировать один и тот же passport
    2) position проставляем автоматически 1..N
    3) sheets_count никогда не NULL
    """
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

    def save(self, commit=True):
        objs = []
        pos = 1

        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
                continue

            obj: ActMaterialItem = form.save(commit=False)
            obj.act = self.instance
            obj.position = pos
            pos += 1

            if obj.sheets_count in (None, ""):
                obj.sheets_count = 1

            if commit:
                obj.save()
                form.save_m2m()

            objs.append(obj)

        if commit:
            for form in self.deleted_forms:
                if form.instance and form.instance.pk:
                    form.instance.delete()

        return objs


ActMaterialFormSet = inlineformset_factory(
    parent_model=Act,
    model=ActMaterialItem,
    form=ActMaterialItemForm,
    formset=BaseActMaterialFormSet,
    extra=0,          # было 1
    can_delete=True,
)


class ActAttachmentForm(forms.ModelForm):
    class Meta:
        model = ActAttachment
        fields = ("title", "doc_no", "doc_date", "sheets_count", "file")
        widgets = {"doc_date": iso_date_widget()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "doc_date" in self.fields:
            force_iso_date_field(self.fields["doc_date"])

        _bootstrapify(self)

    def clean(self):
        cleaned = super().clean()
        sheets = cleaned.get("sheets_count")
        if sheets in (None, ""):
            cleaned["sheets_count"] = 1
        return cleaned


class BaseActAttachmentFormSet(BaseInlineFormSet):
    """
    первая строка — Исполнительная схема
    """
    def __init__(self, *args, act_number: str = "", **kwargs):
        self.act_number = (act_number or "").strip()
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.forms:
            f0 = self.forms[0]
            if hasattr(f0, "cleaned_data") and f0.cleaned_data.get("DELETE"):
                raise ValidationError("Строку 'Исполнительная схема' удалять нельзя.")
            if hasattr(f0, "cleaned_data") and not f0.cleaned_data.get("doc_date"):
                raise ValidationError("Для 'Исполнительной схемы' нужно указать дату.")

    def save(self, commit=True):
        objs = []
        for idx, form in enumerate(self.forms):
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
                continue

            obj: ActAttachment = form.save(commit=False)
            obj.act = self.instance

            if obj.sheets_count in (None, ""):
                obj.sheets_count = 1

            if idx == 0:
                obj.type = AttachmentType.EXEC_SCHEME
                obj.title = "Исполнительная схема"
                obj.doc_no = self.act_number
            else:
                obj.type = AttachmentType.OTHER_QUALITY_DOC

            if commit:
                obj.save()
                form.save_m2m()

            objs.append(obj)

        if commit:
            for form in self.deleted_forms:
                if form.instance and form.instance.pk:
                    form.instance.delete()

        return objs


ActAttachmentFormSet = inlineformset_factory(
    parent_model=Act,
    model=ActAttachment,
    form=ActAttachmentForm,
    formset=BaseActAttachmentFormSet,
    extra=1,
    can_delete=True,
)
