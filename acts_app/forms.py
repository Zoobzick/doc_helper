# acts_app/forms.py
from __future__ import annotations

from typing import Optional

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory

from acts_app.models import Act, ActAttachment, ActMaterialItem, AttachmentType

ISO_DATE_FORMAT = "%Y-%m-%d"


def iso_date_widget():
    return forms.DateInput(attrs={"type": "date"}, format=ISO_DATE_FORMAT)


def force_iso_date_field(field: forms.Field):
    if isinstance(field, forms.DateField):
        if isinstance(field.widget, forms.DateInput):
            field.widget.format = ISO_DATE_FORMAT
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


def _is_concrete_or_mortar(material_name: str) -> bool:
    """
    (material_name) строка, введённая пользователем.
    Ловим "бетон"/"бетонная смесь"/"раствор".
    """
    s = (material_name or "").strip().lower()
    if not s:
        return False
    keywords = ("бетон", "бетонн", "раствор")
    return any(k in s for k in keywords)


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
            "copies_count",
        )
        widgets = {
            "act_date": iso_date_widget(),
            "work_start_date": iso_date_widget(),
            "work_end_date": iso_date_widget(),
            "work_name": forms.Textarea(attrs={"rows": 3}),
            "work_norms_text": forms.Textarea(attrs={"rows": 3}),
            "allow_next_works_text": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for name in ("act_date", "work_start_date", "work_end_date"):
            if name in self.fields:
                force_iso_date_field(self.fields[name])

        if "copies_count" in self.fields:
            w = self.fields["copies_count"].widget
            if not isinstance(w, forms.NumberInput):
                self.fields["copies_count"].widget = forms.NumberInput()
                w = self.fields["copies_count"].widget

            w.attrs.setdefault("min", "1")
            w.attrs.setdefault("max", "9")
            w.attrs.setdefault("style", "max-width: 4.2rem;")
            w.attrs.setdefault("inputmode", "numeric")

        if not self.instance.pk:
            self.fields["work_norms_text"].initial = (
                "СП 122.13330.2012, "
                "СП 70.13330.2012, "
                "СП 120.13330.2012, "
                "СП 48.13330.2019"
            )

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
            "manual_doc_date_text",
            "concrete_volume_m3",  # ✅ ШАГ 1
            "sheets_count",
            "manual_doc_name",
        )
        widgets = {
            "manual_doc_date": iso_date_widget(),
            "manual_doc_date_text": forms.TextInput(attrs={"placeholder": "например: 02.2026 или 26-27.01.2026"}),
            "concrete_volume_m3": forms.NumberInput(attrs={"min": "0", "step": "1", "inputmode": "numeric"}),
        }

    def __init__(self, *args, project_id: Optional[int] = None, **kwargs):
        super().__init__(*args, **kwargs)

        from passports_app.models import Passport  # noqa: WPS433

        self.fields["passport"].queryset = Passport.objects.all().order_by("-id")

        self.fields["sheets_count"].required = False
        if self.initial.get("sheets_count") in (None, ""):
            self.initial["sheets_count"] = 1
        if getattr(self.instance, "sheets_count", None) in (None, ""):
            self.initial["sheets_count"] = 1

        self.fields["concrete_volume_m3"].required = False  # условно (по названию материала)

        if "manual_doc_date" in self.fields:
            force_iso_date_field(self.fields["manual_doc_date"])

        self.fields["manual_name"].label = "Материал"
        self.fields["manual_doc_name"].label = "Наименование документа"
        self.fields["manual_doc_no"].label = "Номер документа"
        self.fields["manual_doc_date"].label = "Дата документа (dd.mm.yyyy)"
        self.fields["manual_doc_date_text"].label = "Дата документа (как в документе)"
        self.fields["manual_doc_date_text"].help_text = "Если заполнено — оно печатается вместо dd.mm.yyyy."
        self.fields["concrete_volume_m3"].label = "Объём, м3"

        _bootstrapify(self)

    def clean(self):
        cleaned = super().clean()

        passport = cleaned.get("passport")  # (passport) паспорт из БД
        manual_name = (cleaned.get("manual_name") or "").strip()

        if passport:
            cleaned["concrete_volume_m3"] = None

        # sheets_count
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

        # manual_doc_date_text: если заполнено — manual_doc_date можно не заполнять (и наоборот)
        date_text = (cleaned.get("manual_doc_date_text") or "").strip()
        cleaned["manual_doc_date_text"] = date_text

        # ✅ ШАГ 1: объём обязателен только для ручных "бетон/раствор"
        if not passport and _is_concrete_or_mortar(manual_name):
            v = cleaned.get("concrete_volume_m3")  # (v) объём, м3
            if v in (None, ""):
                self.add_error("concrete_volume_m3", "Для бетонной смеси/раствора нужно указать объём (целое число).")
            else:
                try:
                    v_int = int(v)
                except (TypeError, ValueError):
                    self.add_error("concrete_volume_m3", "Объём должен быть целым числом.")
                else:
                    if v_int < 0:
                        self.add_error("concrete_volume_m3", "Объём не может быть отрицательным.")
                    cleaned["concrete_volume_m3"] = v_int

        return cleaned


class BaseActMaterialFormSet(BaseInlineFormSet):
    """
    1) не даём дублировать один и тот же passport
    2) position проставляем автоматически 1.N
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
        if commit:
            for form in self.deleted_forms:
                if form.instance and form.instance.pk:
                    form.instance.delete()

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

        return objs


ActMaterialFormSet = inlineformset_factory(
    parent_model=Act,
    model=ActMaterialItem,
    form=ActMaterialItemForm,
    formset=BaseActMaterialFormSet,
    extra=0,
    can_delete=True,
)


class ActAttachmentForm(forms.ModelForm):
    class Meta:
        model = ActAttachment
        fields = ("title", "doc_no", "doc_date", "doc_date_to", "sheets_count", "file", "is_original")
        widgets = {"doc_date": iso_date_widget(), "doc_date_to": iso_date_widget(), "is_original": forms.HiddenInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "doc_date" in self.fields:
            force_iso_date_field(self.fields["doc_date"])
        if "doc_date_to" in self.fields:
            force_iso_date_field(self.fields["doc_date_to"])

        _bootstrapify(self)

        if "title" in self.fields:
            self.fields["title"].widget.attrs.setdefault("class", "")
            self.fields["title"].widget.attrs["class"] += " js-att-title"
        if "is_original" in self.fields:
            self.fields["is_original"].widget.attrs.setdefault("class", "")
            self.fields["is_original"].widget.attrs["class"] += " js-att-original"

    def clean(self):
        cleaned = super().clean()

        sheets = cleaned.get("sheets_count")
        if sheets in (None, ""):
            cleaned["sheets_count"] = 1

        d_from = cleaned.get("doc_date")
        d_to = cleaned.get("doc_date_to")
        if d_from and d_to and d_to < d_from:
            self.add_error("doc_date_to", "Дата (по) не может быть раньше Даты (с).")

        return cleaned


class BaseActAttachmentFormSet(BaseInlineFormSet):
    """
    первая строка — Исполнительная схема

    ВАЖНО:
    реестры (MATERIALS_REGISTRY / DOCS_REGISTRY / APPROVALS_REGISTRY) — системные сущности,
    в форму "Предъявлены документы..." НЕ попадают.
    """

    def __init__(self, *args, act_number: str = "", **kwargs):
        self.act_number = (act_number or "").strip()
        super().__init__(*args, **kwargs)

        exclude_types = [AttachmentType.MATERIALS_REGISTRY]

        if hasattr(AttachmentType, "DOCS_REGISTRY"):
            exclude_types.append(AttachmentType.DOCS_REGISTRY)

        if hasattr(AttachmentType, "APPROVALS_REGISTRY"):
            exclude_types.append(AttachmentType.APPROVALS_REGISTRY)

        self.queryset = self.queryset.exclude(type__in=exclude_types)

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
                obj.title = "исполнительная схема"
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
    extra=0,
    can_delete=True,
)

ActAttachmentCreateFormSet = inlineformset_factory(
    parent_model=Act,
    model=ActAttachment,
    form=ActAttachmentForm,
    formset=BaseActAttachmentFormSet,
    extra=1,
    can_delete=True,
)
