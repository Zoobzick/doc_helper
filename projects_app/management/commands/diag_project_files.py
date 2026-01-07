from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from projects_app.models import Project, ProjectRevision


class Command(BaseCommand):
    help = "Диагностика путей PDF-файлов проекта и соответствия папке PROJECTS_DIR/<full_code>"

    def add_arguments(self, parser):
        parser.add_argument("project_id", type=int, help="ID проекта (Project.id)")
        parser.add_argument(
            "--check-only",
            action="store_true",
            help="Только проверка (без лишней инфы)",
        )

    def handle(self, *args, **options):
        project_id: int = options["project_id"]
        check_only: bool = options["check_only"]

        projects_dir = Path(getattr(settings, "PROJECTS_DIR", "")).resolve()
        if not str(projects_dir) or str(projects_dir) == ".":
            raise CommandError("settings.PROJECTS_DIR не задан или некорректный")

        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            raise CommandError(f"Project id={project_id} не найден")

        expected_dir = (projects_dir / project.full_code).resolve()

        self.stdout.write(self.style.MIGRATE_HEADING("=== PROJECT ==="))
        self.stdout.write(f"project.id: {project.id}")
        self.stdout.write(f"project.full_code: {project.full_code}")
        self.stdout.write(f"PROJECTS_DIR: {projects_dir}")
        self.stdout.write(f"expected_dir: {expected_dir} | exists={expected_dir.exists()}")

        revs = (
            ProjectRevision.objects
            .filter(project=project)
            .order_by("-created_at")
        )

        if not revs.exists():
            self.stdout.write(self.style.WARNING("У проекта нет ревизий"))
            return

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== REVISIONS ==="))

        for rev in revs:
            fp = Path(rev.file_path)
            fp_exists = fp.exists()
            parent = fp.parent
            parent_exists = parent.exists()

            # пытаемся понять: лежит ли файл внутри expected_dir
            try:
                fp.relative_to(expected_dir)
                in_expected = True
            except Exception:
                in_expected = False

            if check_only:
                self.stdout.write(
                    f"rev id={rev.id} r={rev.revision} latest={rev.is_latest} "
                    f"file_exists={fp_exists} in_expected_dir={in_expected} "
                    f"file_path={rev.file_path}"
                )
                continue

            self.stdout.write("-" * 80)
            self.stdout.write(f"rev.id: {rev.id}")
            self.stdout.write(f"rev.revision: {rev.revision} | is_latest={rev.is_latest} | in_production={getattr(rev,'in_production',None)}")
            self.stdout.write(f"rev.sha256: {rev.sha256}")
            self.stdout.write(f"file_path: {rev.file_path}")
            self.stdout.write(f"file_exists: {fp_exists}")
            self.stdout.write(f"parent_dir: {parent} | exists={parent_exists}")
            self.stdout.write(f"in expected_dir: {in_expected}")

            # покажем, что лежит в папке (первые 15)
            if parent_exists:
                try:
                    items = list(parent.iterdir())
                    self.stdout.write(f"parent items: {len(items)}")
                    for p in items[:15]:
                        self.stdout.write(f"  - {p.name}")
                    if len(items) > 15:
                        self.stdout.write("  ...")
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"Не удалось прочитать содержимое parent_dir: {e}"))

        self.stdout.write(self.style.SUCCESS("\nГотово."))

