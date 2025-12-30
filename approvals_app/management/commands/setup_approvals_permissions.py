from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from approvals_app.models import Approval

class Command(BaseCommand):
    help = "Выдаёт permissions группам worker и mark12 для approvals_app"

    def handle(self, *args, **options):
        ct = ContentType.objects.get_for_model(Approval)

        def perm(codename: str) -> Permission:
            return Permission.objects.get(content_type=ct, codename=codename)

        worker, _ = Group.objects.get_or_create(name="worker")
        mark12, _ = Group.objects.get_or_create(name="mark12")

        worker_perms = [
            "view_approvals_done_page",
            "view_approvals_pending_page",
            "add_approvals_done",
            "add_approvals_pending",
            "delete_approvals",
        ]

        mark12_perms = [
            "view_approvals_done_page",
            "view_approvals_pending_page",
            "add_approvals_done",
            "add_approvals_pending",
            "delete_approvals",
        ]

        worker.permissions.add(*[perm(p) for p in worker_perms])
        mark12.permissions.add(*[perm(p) for p in mark12_perms])

        self.stdout.write(self.style.SUCCESS("✅ Permissions выданы группам worker и mark12"))
