from django import forms
from django.contrib.auth.forms import AuthenticationForm

class SimpleLoginForm(AuthenticationForm):
    username = forms.CharField(
        label='Логин',
        widget=forms.TextInput(attrs={'placeholder': 'Введите логин'})
    )
    password = forms.CharField(
        label='Пароль',
        widget=forms.PasswordInput(attrs={'placeholder': 'Введите пароль'})
    )