from __future__ import annotations

from django import forms


class BoxLabelForm(forms.Form):
    DSM = forms.CharField(required=False)
    MIP = forms.CharField(required=False)
    SMU = forms.CharField(required=False)

    # CSV: "1,2,3"
    exec_ids = forms.CharField(required=False)
    work_ids = forms.CharField(required=False)

    def clean_exec_ids(self) -> list[int]:
        return _parse_csv_ids(self.cleaned_data.get("exec_ids", ""))

    def clean_work_ids(self) -> list[int]:
        return _parse_csv_ids(self.cleaned_data.get("work_ids", ""))

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("exec_ids") and not cleaned.get("work_ids"):
            raise forms.ValidationError("Не выбраны проекты (ни ИД, ни РД).")
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
