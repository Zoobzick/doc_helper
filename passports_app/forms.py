from __future__ import annotations

import os
from django import forms


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
