from django.apps import AppConfig


class TransportConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "transport_app"
    verbose_name = "Заявки на автотранспорт"

    def get_profile_settings_form(self, user, data=None):
        from .forms import TransportPreferencesForm
        return TransportPreferencesForm(user=user, data=data, prefix=self.label)
