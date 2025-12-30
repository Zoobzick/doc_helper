from django.core.management.base import BaseCommand
from django.db.models import Count

from projects_app.models import Project, Stage, Plot


class Command(BaseCommand):
    help = "Delete bad projects created by buggy scan (UNKNOWN stage/plot + needs_review + no revisions)"

    def handle(self, *args, **options):
        qs = (
            Project.objects
            .filter(needs_review=True, stage__code="UNKNOWN", plot__code="UNKNOWN")
            .annotate(revisions_count=Count("revisions"))
            .filter(revisions_count=0)
        )

        ids = list(qs.values_list("id", flat=True))
        self.stdout.write(f"Will delete projects: {len(ids)}")
        self.stdout.write(f"Sample ids: {ids[:30]}")

        deleted_total, deleted_map = qs.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted rows: {deleted_total}"))
        self.stdout.write(str(deleted_map))

        # (опционально) подчистим справочники UNKNOWN, если они никем не используются
        for Model, code in ((Stage, "UNKNOWN"), (Plot, "UNKNOWN")):
            obj = Model.objects.filter(code=code).first()
            if not obj:
                continue

            # project_set (reverse relation) — проекты, которые ссылаются на этот справочник
            if obj.project_set.count() == 0:
                obj.delete()
                self.stdout.write(self.style.SUCCESS(f"Deleted orphan {Model.__name__}(code='{code}')"))
            else:
                self.stdout.write(
                    f"Keep {Model.__name__}(code='{code}'): used by {obj.project_set.count()} projects"
                )
