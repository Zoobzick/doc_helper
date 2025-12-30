import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db.models import Q

from projects_app.models import Project, ProjectRevision


class Command(BaseCommand):
    help = "Dump projects_app data for debugging (UNKNOWN/needs_review) to JSON"

    def add_arguments(self, parser):
        parser.add_argument("--out", default="projects_dump_debug.json")
        parser.add_argument("--limit", type=int, default=2000)

    def handle(self, *args, **options):
        out_path = Path(options["out"]).resolve()
        limit = options["limit"]

        # (q_unknown) любой справочник = UNKNOWN
        q_unknown = (
            Q(designer__code="UNKNOWN")
            | Q(line__code="UNKNOWN")
            | Q(design_stage__code="UNKNOWN")
            | Q(stage__code="UNKNOWN")
            | Q(plot__code="UNKNOWN")
            | Q(section__code="UNKNOWN")
        )

        # Вытаскиваем проекты, которые требуют проверки, или содержат UNKNOWN
        projects_qs = (
            Project.objects.select_related(
                "designer", "line", "design_stage", "stage", "plot", "section"
            )
            .filter(Q(needs_review=True) | q_unknown)
            .order_by("id")[:limit]
        )

        project_ids = list(projects_qs.values_list("id", flat=True))

        revisions_qs = (
            ProjectRevision.objects
            .filter(project_id__in=project_ids)
            .order_by("project_id", "-created_at")
        )

        # соберём ревизии пачкой по project_id
        rev_map: dict[int, list[dict]] = {}
        for r in revisions_qs:
            rev_map.setdefault(r.project_id, []).append({
                "revision": r.revision,
                "file_name": r.file_name,
                "file_path": r.file_path,
                "is_latest": r.is_latest,
                "created_at": r.created_at.isoformat(),
            })

        data = []
        for p in projects_qs:
            data.append({
                "id": p.id,
                "full_code": p.full_code,
                "needs_review": p.needs_review,
                "construction": p.construction,
                "internal_code": p.internal_code,
                "designer": p.designer.code,
                "line": p.line.code,
                "design_stage": p.design_stage.code,
                "stage": p.stage.code,
                "plot": p.plot.code,
                "section": p.section.code,
                "number": p.number,
                "revisions": rev_map.get(p.id, []),
            })

        payload = {
            "projects_count": len(data),
            "filters": ["needs_review=True OR any(code='UNKNOWN')"],
            "projects": data,
        }

        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"OK: wrote {len(data)} projects -> {out_path}"))
