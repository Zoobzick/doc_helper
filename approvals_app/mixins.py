from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

def _in_group(user, name: str) -> bool:
    return user.is_authenticated and user.groups.filter(name=name).exists()


class WorkerOnlyMixin(LoginRequiredMixin):
    login_url = "/login/"

    def dispatch(self, request, *args, **kwargs):
        u = request.user
        if u.is_superuser or _in_group(u, "worker"):
            return super().dispatch(request, *args, **kwargs)
        raise PermissionDenied


class PendingAccessMixin(LoginRequiredMixin):
    login_url = "/login/"

    def dispatch(self, request, *args, **kwargs):
        u = request.user
        if u.is_superuser or _in_group(u, "worker") or _in_group(u, "mark12"):
            return super().dispatch(request, *args, **kwargs)
        raise PermissionDenied
