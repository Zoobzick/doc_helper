from __future__ import annotations

from django import forms

from .models import Organization, Person, PersonNRS


class _BootstrapModelForm(forms.ModelForm):
    """
    Делает формы визуально нормальными для Bootstrap/NiceAdmin:
    - text/number/date -> form-control
    - select -> form-select
    - checkbox -> form-check-input
    - textarea -> form-control + rows
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for name, field in self.fields.items():
            widget = field.widget

            # Чтобы поля не были "узкими" и выглядели современно
            base_classes = widget.attrs.get("class", "").split()

            if isinstance(widget, (forms.TextInput, forms.EmailInput, forms.NumberInput, forms.URLInput, forms.DateInput, forms.DateTimeInput, forms.PasswordInput)):
                base_classes += ["form-control"]
                widget.attrs.setdefault("placeholder", field.label)
            elif isinstance(widget, forms.Textarea):
                base_classes += ["form-control"]
                widget.attrs.setdefault("rows", 3)
                widget.attrs.setdefault("placeholder", field.label)
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                base_classes += ["form-select"]
            elif isinstance(widget, forms.CheckboxInput):
                base_classes += ["form-check-input"]

            # Убираем дубли и сохраняем
            widget.attrs["class"] = " ".join(dict.fromkeys(base_classes)).strip()


class OrganizationForm(_BootstrapModelForm):
    class Meta:
        model = Organization
        fields = [
            "full_name",
            "short_name",
            "ogrn",
            "inn",
            "address",
            "tel_fax",
            "sro",
            "sro_ogrn",
            "sro_inn",
            "is_active",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 4}),
        }


class PersonForm(_BootstrapModelForm):
    class Meta:
        model = Person
        fields = ["last_name", "first_name", "middle_name", "is_active"]


class PersonNRSForm(_BootstrapModelForm):
    class Meta:
        model = PersonNRS
        fields = ["person", "nrs_id", "valid_from", "valid_to", "is_active"]
        widgets = {
            "valid_from": forms.DateInput(attrs={"type": "date"}),
            "valid_to": forms.DateInput(attrs={"type": "date"}),
        }
