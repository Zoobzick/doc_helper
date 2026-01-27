from django import forms
from .models import Directive, Authorization


class DirectiveForm(forms.ModelForm):
    class Meta:
        model = Directive
        fields = (
            "doc_type",
            "number",
            "date",
            "issuer_organization",
            "pdf_file",
            "note",
            "is_active",
        )
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # чтобы инпут type="date" нормально принимал ввод
        self.fields["date"].input_formats = ["%Y-%m-%d"]


class AuthorizationForm(forms.ModelForm):
    class Meta:
        model = Authorization
        fields = (
            "organization",
            "person",
            "role",
            "position_text",
            "valid_from",
            "valid_to",
            "is_active",
        )
        widgets = {
            "valid_from": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "valid_to": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["valid_from"].input_formats = ["%Y-%m-%d"]
        self.fields["valid_to"].input_formats = ["%Y-%m-%d"]
