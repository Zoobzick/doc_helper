from django import forms

class DocumentStructureUploadForm(forms.Form):
    DOC_TYPES = (
        ("docx", "DOCX (.docx)"),
        ("xlsx", "XLSX (.xlsx)"),
    )

    doc_type = forms.ChoiceField(label="Тип документа", choices=DOC_TYPES)
    file = forms.FileField(label="Загрузить файл (.docx или .xlsx)")

    def clean(self):
        cleaned = super().clean()

        doc_type = cleaned.get("doc_type")  # (doc_type) — выбранный тип: docx/xlsx
        f = cleaned.get("file")             # (f) — загруженный файл

        if not doc_type or not f:
            return cleaned

        name = (f.name or "").lower()

        allowed_suffix = f".{doc_type}"
        if not name.endswith(allowed_suffix):
            raise forms.ValidationError(f"Нужен файл {allowed_suffix}")

        # защита от огромных файлов (например 20 МБ)
        if f.size > 20 * 1024 * 1024:
            raise forms.ValidationError("Файл слишком большой (макс 20 МБ)")

        return cleaned