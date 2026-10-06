from django import forms
from copy import deepcopy
from django.db import transaction
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import TransportItem, TransportRequest, TransportUserSettings
from .formatting import place_numbers


class StyledForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control"})


class TransportPreferencesForm(forms.ModelForm):
    class Meta:
        model = TransportUserSettings
        fields = ["skip_general"]
        widgets = {"skip_general": forms.CheckboxInput(attrs={"class": "form-check-input"})}
        help_texts = {"skip_general": "Открывать новую заявку сразу на этапе транспорта. Используются сохранённые общие данные и текущая дата. Если данные ещё не заполнены, откроется первый этап. К нему всегда можно вернуться."}

    def __init__(self, *, user, **kwargs):
        self.user = user
        super().__init__(instance=TransportUserSettings.objects.filter(user=user).first(), **kwargs)
        source = RequestForm()
        self.default_names = [name for name in RequestForm.Meta.fields if name != "date"]
        for name in self.default_names:
            field = deepcopy(source.fields[name])
            field.required = False
            field.initial = self.instance.values.get(name, "")
            self.fields[name] = field

    @property
    def groups(self):
        return [
            {"title": "Создание заявки", "fields": [self["skip_general"]]},
            {"title": "Общие данные по умолчанию", "fields": [self[name] for name in ("organization", "site", "place", "phone")]},
            {"title": "Ответственные", "fields": [self[name] for name in ("author", "reviewer", "approver")]},
        ]

    def clean_place(self):
        value = self.cleaned_data["place"]
        if value and not place_numbers(value):
            raise forms.ValidationError("Укажите номера площадок через запятую.")
        return value

    def save(self):
        with transaction.atomic():
            obj, _ = TransportUserSettings.objects.select_for_update().get_or_create(user=self.user)
            values = dict(obj.values)
            for name in self.default_names:
                if self.add_prefix(name) in self.data:
                    values[name] = self.cleaned_data[name]
            obj.values = values
            obj.skip_general = self.cleaned_data["skip_general"]
            obj.save(update_fields=["values", "skip_general"])
        return obj


class RequestForm(StyledForm):
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=1, initial=1)

    class Meta:
        model = TransportRequest
        fields = ["organization", "site", "place", "phone", "date", "author", "reviewer", "approver"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].initial = self.instance.revision
        self.fields["place"].label = "Номер площадки / номера площадок"
        self.fields["place"].widget.attrs["placeholder"] = "6 или 6, 1, 7"
        self.fields["place"].help_text = "Несколько номеров укажите через запятую. В бланке: Площадки №6, №1, №7."
        for name in ("author", "reviewer", "approver"):
            self.fields[name].help_text = "Разделяйте должность и ФИО запятой: Начальник участка, Иванов И.И."

    def clean_place(self):
        value = self.cleaned_data["place"]
        if not place_numbers(value):
            raise forms.ValidationError("Укажите хотя бы один номер площадки.")
        return value


class ItemForm(StyledForm):
    class Meta:
        model = TransportItem
        fields = ["vehicle", "quantity", "mode", "start", "end", "shift", "frequency", "work", "note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["end"].required = False

    def clean(self):
        data = super().clean()
        if data.get("mode") == "day":
            data["end"] = data.get("start")
            data["frequency"] = "daily"
        elif not data.get("end"):
            self.add_error("end", "Укажите дату окончания периода.")
        return data


class ItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if not any(f.cleaned_data and not f.cleaned_data.get("DELETE") for f in self.forms):
            raise forms.ValidationError("Добавьте хотя бы одну позицию транспорта.")


Items = inlineformset_factory(TransportRequest, TransportItem, form=ItemForm, formset=ItemFormSet,
                             extra=0, can_delete=True, max_num=100, validate_max=True, absolute_max=100)
