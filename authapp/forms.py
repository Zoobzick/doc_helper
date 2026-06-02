from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from orgs_app.models import Organization
from projects_app.models import Line, Stage


class LoginForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "Введите email"}),
    )
    password = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Введите пароль"}),
    )
    remember = forms.BooleanField(required=False, label="Запомнить меня")


class RegisterForm(forms.Form):
    first_name = forms.CharField(
        label="Имя",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Имя"}),
    )
    last_name = forms.CharField(
        label="Фамилия",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Фамилия"}),
    )
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "Email"}),
    )
    password1 = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Пароль"}),
    )
    password2 = forms.CharField(
        label="Повторите пароль",
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Повторите пароль"}),
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        User = get_user_model()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Пользователь с таким email уже существует.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Пароли не совпадают.")
        return cleaned


class ProfileForm(forms.ModelForm):
    lines = forms.ModelMultipleChoiceField(
        label="Рабочие линии",
        queryset=Line.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    stages = forms.ModelMultipleChoiceField(
        label="Рабочие этапы",
        queryset=Stage.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = get_user_model()
        fields = ("first_name", "last_name", "organization", "lines", "stages")
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "organization": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {
            "first_name": "Имя",
            "last_name": "Фамилия",
            "organization": "Организация",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        organization_filter = Q(is_active=True)
        line_filter = Q(is_active=True)
        stage_filter = Q(is_active=True)
        if self.instance and self.instance.pk:
            if self.instance.organization_id:
                organization_filter |= Q(pk=self.instance.organization_id)
            line_filter |= Q(pk__in=self.instance.lines.values_list("pk", flat=True))
            stage_filter |= Q(pk__in=self.instance.stages.values_list("pk", flat=True))

        self.fields["organization"].queryset = (
            Organization.objects.filter(organization_filter).distinct().order_by("short_name")
        )
        self.fields["organization"].empty_label = "Не выбрана"
        self.fields["lines"].queryset = Line.objects.filter(line_filter).distinct().order_by("code")
        self.fields["stages"].queryset = Stage.objects.filter(stage_filter).distinct().order_by("code")
