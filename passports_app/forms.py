from __future__ import annotations

import os
from django import forms

from .models import Passport, Material


ALLOWED_FILE_EXTS = {"pdf", "psd", "xlsx", "docx"}
ALLOWED_ARCHIVE_EXTS = {"zip"}


class PassportUploadForm(forms.Form):
    """
    ОДНА форма для:
    - одиночного файла паспорта (pdf/psd/xlsx/docx)
    - архива (zip)
    """
    file = forms.FileField(label="Файл (паспорт или ZIP архив)", required=True)

    # поля для одиночного файла (при zip игнорируются)
    material = forms.CharField(
        label="Материал",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Напр.: Труба 45х3"}),
    )
    document_name = forms.CharField(
        label="Наименование документа",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Напр.: сертификат качества и количества"}),
    )
    document_number = forms.CharField(
        label="Номер документа",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Напр.: 1710087457"}),
    )
    document_date = forms.DateField(
        label="Дата документа",
        required=False,
        input_formats=["%Y-%m-%d", "%d.%m.%Y"],
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )

    # поведение кнопок (только для одиночного файла)
    action = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean_file(self):
        f = self.cleaned_data["file"]
        ext = os.path.splitext(f.name)[1].lower().lstrip(".")
        if ext not in (ALLOWED_FILE_EXTS | ALLOWED_ARCHIVE_EXTS):
            raise forms.ValidationError(
                "Неподдерживаемый формат. Разрешено: "
                + ", ".join(sorted(ALLOWED_FILE_EXTS | ALLOWED_ARCHIVE_EXTS))
            )
        return f


class PassportUpdateForm(forms.ModelForm):
    """
    Редактирование паспорта на странице деталей:
    - материал редактируем строкой (material_name), а в save() привязываем/создаём Material.
    """
    material_name = forms.CharField(
        label="Материал",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Напр.: Труба 45х3"}),
    )

    class Meta:
        model = Passport
        fields = ("document_name", "document_number", "document_date")
        widgets = {
            "document_name": forms.TextInput(attrs={"class": "form-control"}),
            "document_number": forms.TextInput(attrs={"class": "form-control"}),

            # ✅ FIX: нужен format для <input type="date">
            "document_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": "form-control", "type": "date"},
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["document_date"].input_formats = ["%Y-%m-%d", "%d.%m.%Y"]

        # (passport_instance) текущий объект, чтобы заполнить material_name из FK material
        passport_instance: Passport | None = kwargs.get("instance")
        if passport_instance and passport_instance.material_id:
            self.fields["material_name"].initial = passport_instance.material.name

    def save(self, commit: bool = True) -> Passport:
        passport: Passport = super().save(commit=False)

        # (material_name) строка от пользователя
        material_name = (self.cleaned_data.get("material_name") or "").strip()
        if material_name:
            material, _ = Material.objects.get_or_create(name=material_name)
            passport.material = material
        else:
            passport.material = None

        # (needs_review) можно простое правило: если не все ключевые поля заполнены — требует проверки
        passport.needs_review = not bool(
            passport.material_id and passport.document_name and passport.document_number and passport.document_date
        )

        if commit:
            passport.save()

        return passport
