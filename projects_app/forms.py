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
        label="Шифр проекта",
        required=True,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    construction = forms.CharField(
        label="Стройка",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    def clean_full_code(self):
        value = (self.cleaned_data.get("full_code") or "").strip()
        value = " ".join(value.split())
        if not value:
            raise forms.ValidationError("Введите полный шифр проекта.")
        return value


class ProjectUpdateForm(forms.ModelForm):
    """
    ВАЖНО:
    full_code в модели уникальный, но в бизнес-логике ты допускаешь:
    - пользователь вводит full_code уже существующего проекта
    - мы должны сделать MERGE (ревизии объединить), а не падать.
    Поэтому для UpdateForm мы исключаем full_code из validate_unique().
    """

    full_code = forms.CharField(
        label="Шифр проекта",
        required=False,  # можно оставить пустым, если у тебя бывают черновики
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Project
        fields = (
            "full_code",
            "construction",
            "designer",
            "line",
            "design_stage",
            "stage",
            "plot",
            "section",
        )

        widgets = {
            "construction": forms.TextInput(attrs={"class": "form-control"}),
            "designer": forms.Select(attrs={"class": "form-select"}),
            "line": forms.Select(attrs={"class": "form-select"}),
            "design_stage": forms.Select(attrs={"class": "form-select"}),
            "stage": forms.Select(attrs={"class": "form-select"}),
            "plot": forms.Select(attrs={"class": "form-select"}),
            "section": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # только активные справочники
        self.fields["designer"].queryset = Designer.objects.filter(is_active=True).order_by("code")
        self.fields["line"].queryset = Line.objects.filter(is_active=True).order_by("code")
        self.fields["design_stage"].queryset = DesignStage.objects.filter(is_active=True).order_by("code")
        self.fields["stage"].queryset = Stage.objects.filter(is_active=True).order_by("code")
        self.fields["plot"].queryset = Plot.objects.filter(is_active=True).order_by("code")
        self.fields["section"].queryset = Section.objects.filter(is_active=True).order_by("code")

        for name in ("designer", "line", "design_stage", "stage", "plot", "section"):
            self.fields[name].empty_label = "— выберите —"

        self.fields["full_code"].help_text = (
            "Можно изменить шифр. Если шифр уже существует — ревизии будут объединены."
        )

    def clean_full_code(self):
        value = (self.cleaned_data.get("full_code") or "").strip()
        value = " ".join(value.split())
        # разрешаем пустое (если у тебя реально бывают черновики)
        # если хочешь запретить пустое — раскомментируй:
        # if not value:
        #     raise forms.ValidationError("Введите полный шифр проекта.")
        return value

    def validate_unique(self):
        """
        Отключаем уникальность full_code на уровне формы.
        В Django exclude может быть set — работаем через set-логику.
        """
        exclude = set(self._get_validation_exclusions())
        exclude.add("full_code")
        self.instance.validate_unique(exclude=exclude)

