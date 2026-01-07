from django import forms

from .models import (
    Project,
    Designer,
    Line,
    DesignStage,
    Stage,
    Plot,
    Section,
)


class ProjectCreateForm(forms.Form):
    upload_id = forms.UUIDField(required=True, widget=forms.HiddenInput())

    full_code = forms.CharField(
        label="Полный шифр проекта",
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "ИМИП-МРАЛ1-Р-Г0200-СТ06-001-01-КЖ16",
            }
        ),
    )

    construction = forms.CharField(
        label="Описание / конструкция",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )

    def clean_full_code(self):
        value = (self.cleaned_data.get("full_code") or "").strip()
        value = " ".join(value.split())
        if not value:
            raise forms.ValidationError("Введите полный шифр проекта.")
        return value



class ProjectUpdateForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = (
            "full_code",
            "designer",
            "line",
            "design_stage",
            "stage",
            "plot",
            "section",
            "construction",
        )
        widgets = {
            "full_code": forms.TextInput(attrs={"class": "form-control"}),

            "designer": forms.Select(attrs={"class": "form-select"}),
            "line": forms.Select(attrs={"class": "form-select"}),
            "design_stage": forms.Select(attrs={"class": "form-select"}),
            "stage": forms.Select(attrs={"class": "form-select"}),
            "plot": forms.Select(attrs={"class": "form-select"}),
            "section": forms.Select(attrs={"class": "form-select"}),

            "construction": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["designer"].queryset = Designer.objects.filter(is_active=True).order_by("code")
        self.fields["line"].queryset = Line.objects.filter(is_active=True).order_by("code")
        self.fields["design_stage"].queryset = DesignStage.objects.filter(is_active=True).order_by("code")
        self.fields["stage"].queryset = Stage.objects.filter(is_active=True).order_by("code")
        self.fields["plot"].queryset = Plot.objects.filter(is_active=True).order_by("code")
        self.fields["section"].queryset = Section.objects.filter(is_active=True).order_by("code")

        for name in ("designer", "line", "design_stage", "stage", "plot", "section"):
            self.fields[name].empty_label = "— выберите —"

        # подсказка пользователю
        self.fields["full_code"].help_text = "Можно изменить шифр. Если шифр уже существует — ревизии будут объединены."
