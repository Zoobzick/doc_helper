from __future__ import annotations

from django import forms

from documents_app.models import (
    BatchAttachmentType,
    DocumentBatchDocumentationType,
    DocumentBatchGenerationMode,
    DocumentBatchLetterType,
    DocumentBatchProjectScope,
    DocumentBatchSelectionMode,
)
from projects_app.models import Project


class BoxLabelForm(forms.Form):
    DSM = forms.CharField(required=False)
    MIP = forms.CharField(required=False)
    SMU = forms.CharField(required=False)

    # CSV: "1,2,3"
    exec_ids = forms.CharField(required=False)
    work_ids = forms.CharField(required=False)

    # РІС‹Р±СЂР°РЅРЅС‹Р№ СЌС‚Р°Рї РёР· dropdown
    stage_id = forms.IntegerField(required=False)

    def clean_exec_ids(self) -> list[int]:
        return _parse_csv_ids(self.cleaned_data.get("exec_ids", ""))

    def clean_work_ids(self) -> list[int]:
        return _parse_csv_ids(self.cleaned_data.get("work_ids", ""))

    def clean(self):
        cleaned = super().clean()

        exec_ids = cleaned.get("exec_ids") or []
        work_ids = cleaned.get("work_ids") or []

        if not exec_ids and not work_ids:
            raise forms.ValidationError("РќРµ РІС‹Р±СЂР°РЅС‹ РїСЂРѕРµРєС‚С‹ (РЅРё РР”, РЅРё Р Р”).")

        return cleaned


class DocumentBatchMasterForm(forms.Form):
    title = forms.CharField(
        required=False,
        label="РќР°Р·РІР°РЅРёРµ РєРѕРјРїР»РµРєС‚Р°",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "РќР°РїСЂРёРјРµСЂ: РљРѕРјРїР»РµРєС‚ РР” Р·Р° РјР°СЂС‚ 2026",
            }
        ),
    )

    comment = forms.CharField(
        required=False,
        label="РљРѕРјРјРµРЅС‚Р°СЂРёР№",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "РќРµРѕР±СЏР·Р°С‚РµР»СЊРЅС‹Р№ РєРѕРјРјРµРЅС‚Р°СЂРёР№",
            }
        ),
    )

    selection_mode = forms.ChoiceField(
        label="Р РµР¶РёРј РѕС‚Р±РѕСЂР°",
        choices=DocumentBatchSelectionMode.choices,
        initial=DocumentBatchSelectionMode.ALL_TIME,
        widget=forms.RadioSelect,
    )

    month_from = forms.CharField(
        required=False,
        label="РњРµСЃСЏС† РѕС‚",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "03.2026",
            }
        ),
    )

    month_to = forms.CharField(
        required=False,
        label="РњРµСЃСЏС† РґРѕ",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "03.2026",
            }
        ),
    )

    generation_mode = forms.ChoiceField(
        label="Р§С‚Рѕ СЃС„РѕСЂРјРёСЂРѕРІР°С‚СЊ",
        choices=DocumentBatchGenerationMode.choices,
        initial=DocumentBatchGenerationMode.FULL_SET,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    letter_type = forms.ChoiceField(
        label="РўРёРї РїРёСЃСЊРјР°",
        choices=DocumentBatchLetterType.choices,
        initial=DocumentBatchLetterType.FOR_EXECUTION,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    letter_number = forms.CharField(
        required=False,
        label="РќРѕРјРµСЂ РїРёСЃСЊРјР°",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "РќР°РїСЂРёРјРµСЂ: 15/РР”-2026",
            }
        ),
    )

    letter_date = forms.DateField(
        required=False,
        label="Р”Р°С‚Р° РїРёСЃСЊРјР°",
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={
                "class": "form-control",
                "type": "date",
            }
        ),
        input_formats=["%Y-%m-%d"],
    )

    documentation_type = forms.ChoiceField(
        label="РўРёРї РґРѕРєСѓРјРµРЅС‚Р°С†РёРё",
        choices=DocumentBatchDocumentationType.choices,
        initial=DocumentBatchDocumentationType.ID,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    project_scope = forms.ChoiceField(
        label="Р’С‹Р±РѕСЂ С€РёС„СЂРѕРІ",
        choices=DocumentBatchProjectScope.choices,
        initial=DocumentBatchProjectScope.AUTO_BY_PERIOD,
        widget=forms.RadioSelect,
    )

    one_project = forms.ModelChoiceField(
        required=False,
        label="РџСЂРѕРµРєС‚",
        queryset=Project.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    multiple_projects = forms.ModelMultipleChoiceField(
        required=False,
        label="РџСЂРѕРµРєС‚С‹",
        queryset=Project.objects.none(),
        widget=forms.SelectMultiple(
            attrs={
                "class": "form-select",
                "size": 12,
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        project_qs = (
            Project.objects
            .select_related("line", "stage", "plot")
            .order_by("full_code", "id")
        )

        self.fields["one_project"].queryset = project_qs
        self.fields["multiple_projects"].queryset = project_qs

        self.fields["selection_mode"].widget.attrs.update(
            {"class": "batch-radio-list"}
        )
        self.fields["project_scope"].widget.attrs.update(
            {"class": "batch-radio-list"}
        )

    def clean(self):
        cleaned = super().clean()

        selection_mode = cleaned.get("selection_mode")
        generation_mode = cleaned.get("generation_mode")
        project_scope = cleaned.get("project_scope")

        month_from = (cleaned.get("month_from") or "").strip()
        month_to = (cleaned.get("month_to") or "").strip()

        letter_number = (cleaned.get("letter_number") or "").strip()
        letter_date = cleaned.get("letter_date")

        one_project = cleaned.get("one_project")
        multiple_projects = cleaned.get("multiple_projects")

        if selection_mode == DocumentBatchSelectionMode.RANGE:
            if not month_from:
                self.add_error("month_from", "Р—Р°РїРѕР»РЅРёС‚Рµ РЅР°С‡Р°Р»СЊРЅС‹Р№ РјРµСЃСЏС† РїРµСЂРёРѕРґР°.")
            if not month_to:
                self.add_error("month_to", "Р—Р°РїРѕР»РЅРёС‚Рµ РєРѕРЅРµС‡РЅС‹Р№ РјРµСЃСЏС† РїРµСЂРёРѕРґР°.")
        else:
            cleaned["month_from"] = ""
            cleaned["month_to"] = ""

        needs_letter_fields = generation_mode in {
            DocumentBatchGenerationMode.LETTER_ONLY,
            DocumentBatchGenerationMode.FULL_SET,
        }

        if needs_letter_fields:
            if not letter_number:
                self.add_error("letter_number", "Р”Р»СЏ РІС‹Р±СЂР°РЅРЅРѕРіРѕ СЂРµР¶РёРјР° РЅСѓР¶РµРЅ РЅРѕРјРµСЂ РїРёСЃСЊРјР°.")
            if not letter_date:
                self.add_error("letter_date", "Р”Р»СЏ РІС‹Р±СЂР°РЅРЅРѕРіРѕ СЂРµР¶РёРјР° РЅСѓР¶РЅР° РґР°С‚Р° РїРёСЃСЊРјР°.")
        else:
            cleaned["letter_number"] = ""
            cleaned["letter_date"] = None

        selected_project_ids: list[int] = []

        if project_scope == DocumentBatchProjectScope.ONE_PROJECT:
            if not one_project:
                self.add_error("one_project", "Р’С‹Р±РµСЂРёС‚Рµ РѕРґРёРЅ РїСЂРѕРµРєС‚.")
            else:
                selected_project_ids = [one_project.id]

        elif project_scope == DocumentBatchProjectScope.MULTI_PROJECT:
            if not multiple_projects:
                self.add_error("multiple_projects", "Р’С‹Р±РµСЂРёС‚Рµ С…РѕС‚СЏ Р±С‹ РѕРґРёРЅ РїСЂРѕРµРєС‚.")
            else:
                selected_project_ids = [project.id for project in multiple_projects]

        elif project_scope == DocumentBatchProjectScope.AUTO_BY_PERIOD:
            selected_project_ids = []

        cleaned["selected_project_ids"] = selected_project_ids
        return cleaned


def _parse_csv_ids(value: str) -> list[int]:
    value = (value or "").strip()
    if not value:
        return []

    out: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return out


class BatchAttachmentUploadForm(forms.Form):
    attachment_type = forms.ChoiceField(
        choices=BatchAttachmentType.choices,
        widget=forms.HiddenInput(),
    )
    file = forms.FileField(
        label="PDF-файл",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": ".pdf,application/pdf",
            }
        ),
    )

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]
        name = (uploaded_file.name or "").lower()
        if not name.endswith(".pdf"):
            raise forms.ValidationError("Разрешена загрузка только PDF-файлов.")
        return uploaded_file
