from django import forms

from .models import Project


class ProjectCreateForm(forms.ModelForm):
    upload_id = forms.UUIDField(required=True, widget=forms.HiddenInput())

    class Meta:
        model = Project
        fields = [
            "designer",
            "line",
            "design_stage",
            "stage",
            "plot",
            "internal_code",
            "section",
            "number",
            "construction",
            "needs_review",
        ]
        widgets = {
            "designer": forms.Select(attrs={"class": "form-select"}),
            "line": forms.Select(attrs={"class": "form-select"}),
            "design_stage": forms.Select(attrs={"class": "form-select"}),
            "stage": forms.Select(attrs={"class": "form-select"}),
            "plot": forms.Select(attrs={"class": "form-select"}),
            "section": forms.Select(attrs={"class": "form-select"}),

            "internal_code": forms.TextInput(attrs={"class": "form-control"}),
            "number": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "construction": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "needs_review": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("designer", "line", "design_stage", "stage", "plot", "section"):
            self.fields[name].empty_label = "— выберите —"
