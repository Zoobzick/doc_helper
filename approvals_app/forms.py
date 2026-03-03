from django import forms
from .models import Approval
from projects_app.models import Project


class ApprovalForm(forms.ModelForm):
    project = forms.ModelChoiceField(
        queryset=Project.objects.all().order_by("id"),
        required=False,
        empty_label="— Общее согласование (без проекта) —",
        widget=forms.Select(attrs={"class": "form-select js-project-select"}),
        label="Проект (необязательно)",
    )

    class Meta:
        model = Approval
        fields = ["project", "construction", "description", "file"]
        widgets = {
            "construction": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "file": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "application/pdf"}),
        }
