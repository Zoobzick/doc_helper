from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Tuple

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from .models import Project, ProjectRevision


# =========================================================
# FULL_CODE
# =========================================================

def normalize_full_code(value: str) -> str:
    """
    (value) ввод пользователя
    Возвращает "нормализованный" шифр:
    - обрезаем края
    - сжимаем множественные пробелы
    """
    value = (value or "").strip()
    value = " ".join(value.split())
    return value


# =========================================================
# NEEDS_REVIEW ЛОГИКА
# =========================================================

REQUIRED_PROJECT_FIELDS = (
    "full_code",
    "designer",
    "line",
    "design_stage",
    "stage",
    "plot",
    "section",
)


def compute_needs_review(project: Project) -> bool:
    """
    Возвращает True, если проект "требует заполнения":
    - нет полного шифра (full_code)
    - или не заполнены обязательные классификаторы
    """
    for field in REQUIRED_PROJECT_FIELDS:
        val = getattr(project, field)
        if val is None:
            return True
        if isinstance(val, str) and not val.strip():
            return True
    return False


def sync_needs_review(project: Project, *, save: bool = True) -> bool:
    """
    (project) проект
    (save) нужно ли сохранять в БД

    Пересчитывает project.needs_review и, если надо, сохраняет.
    Возвращает итоговое значение.
    """
    new_value = compute_needs_review(project)
    if project.needs_review != new_value:
        project.needs_review = new_value
        if save:
            project.save(update_fields=["needs_review"])
    return project.needs_review


# =========================================================
# РЕВИЗИИ
# =========================================================

def _next_revision_code(project: Project) -> str:
    """
    Возвращает следующий код ревизии "01", "02", ...
    """
    max_rev = (
        ProjectRevision.objects
        .filter(project=project)
        .aggregate(m=Max("revision"))
        .get("m")
    )

    if not max_rev:
        return "01"

    try:
        n = int(max_rev)
    except ValueError:
        # если по какой-то причине ревизия была нечисловой
        n = 0
    return f"{n + 1:02d}"


@transaction.atomic
def attach_revision_to_project(
        *,
        project: Project,
        file_name: str,
        file_path: str,
        sha256: str | None,
) -> Tuple[ProjectRevision, bool]:
    """
    Добавляет ревизию к проекту:
    - все старые is_latest=False
    - новая rev становится is_latest=True
    - дедуп по sha256 внутри проекта

    Возвращает: (revision, created)
      created=False если sha256 уже существует у проекта
    """
    if sha256 and ProjectRevision.objects.filter(project=project, sha256=sha256).exists():
        existing = (
            ProjectRevision.objects
            .filter(project=project, sha256=sha256)
            .order_by("-created_at")
            .first()
        )
        return existing, False

    ProjectRevision.objects.filter(project=project, is_latest=True).update(is_latest=False)

    revision_code = _next_revision_code(project)
    rev = ProjectRevision.objects.create(
        project=project,
        revision=revision_code,
        file_name=file_name,
        file_path=file_path,
        sha256=sha256,
        is_latest=True,
    )

    return rev, True


@transaction.atomic
def set_revision_in_production(*, revision_id: int, value: bool) -> ProjectRevision:
    """
    (revision_id) id ревизии
    (value) True/False

    Просто переключает поле in_production.
    """
    rev = ProjectRevision.objects.select_for_update().get(id=revision_id)
    rev.in_production = value
    rev.save(update_fields=["in_production"])
    return rev


@transaction.atomic
def delete_project_revision(*, revision_id: int) -> None:
    """
    Удаляет ревизию + файл.
    Если удалили актуальную — назначаем актуальной последнюю по created_at.
    Если ревизий не осталось — удаляем проект.
    """
    rev = ProjectRevision.objects.select_related("project").select_for_update().get(id=revision_id)
    project = rev.project

    file_path = Path(rev.file_path)
    rev.delete()

    # удаляем файл (если есть)
    try:
        file_path.unlink(missing_ok=True)
    except Exception:
        pass

    remaining = ProjectRevision.objects.filter(project=project).order_by("-created_at")
    if not remaining.exists():
        project.delete()
        return

    # если актуальной больше нет — назначим
    if not remaining.filter(is_latest=True).exists():
        newest = remaining.first()
        ProjectRevision.objects.filter(project=project, is_latest=True).update(is_latest=False)
        newest.is_latest = True
        newest.save(update_fields=["is_latest"])


@transaction.atomic
def assign_full_code_to_draft(*, draft_project_id: int, full_code_input: str) -> Project:
    """
    Назначает full_code "черновику" (или проекту без шифра).
    Если проект с таким full_code уже существует:
      - переносим ревизии на него
      - удаляем черновик
    """
    full_code = normalize_full_code(full_code_input)

    draft = Project.objects.select_for_update().get(id=draft_project_id)

    existing = Project.objects.filter(full_code=full_code).exclude(id=draft.id).first()
    if existing:
        # переносим ревизии
        ProjectRevision.objects.filter(project=draft).update(project=existing)
        draft.delete()

        # пересчёт needs_review на целевом проекте
        sync_needs_review(existing, save=True)
        return existing

    draft.full_code = full_code
    draft.save(update_fields=["full_code"])

    # после назначения шифра пересчитаем needs_review (скорее всего останется True, пока не заполнены классификаторы)
    sync_needs_review(draft, save=True)
    return draft


def process_single_pdf(
        *,
        pdf_path: Path,
        original_name: str,
        user,
) -> dict:
    """
    Обрабатывает один PDF:
    - sha256
    - дедуп
    - проект / ревизия
    """

    # --- sha256 ---
    h = hashlib.sha256()
    with pdf_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    sha256 = h.hexdigest()

    if ProjectRevision.objects.filter(sha256=sha256).exists():
        return {
            "status": "duplicate",
            "file": original_name,
        }

    # --- временный full_code из имени файла ---
    full_code = pdf_path.stem.strip()

    project, created = Project.objects.get_or_create(
        full_code=full_code,
        defaults={
            "needs_review": True,
        },
    )

    project_dir = Path(settings.PROJECTS_DIR) / project.full_code
    project_dir.mkdir(parents=True, exist_ok=True)

    target_path = project_dir / original_name
    pdf_path.replace(target_path)

    revision, created_rev = attach_revision_to_project(
        project=project,
        file_name=original_name,
        file_path=str(target_path),
        sha256=sha256,
    )

    sync_needs_review(project, save=True)

    return {
        "status": "created" if created_rev else "exists",
        "project": project.full_code,
        "revision": revision.revision,
        "file": original_name,
    }


@transaction.atomic
def change_project_full_code(project: Project, new_full_code_input: str) -> Project:




    new_full_code = " ".join((new_full_code_input or "").strip().split())
    print(f"[change_project_full_code] project.id={project.id} old='{project.full_code}' new='{new_full_code}'")
    if not new_full_code:
        raise ValueError("new_full_code пустой")

    if project.full_code == new_full_code:
        return project

    projects_root = Path(settings.PROJECTS_DIR).resolve()
    print(f"[change_project_full_code] PROJECTS_DIR={projects_root}")
    new_dir = (projects_root / new_full_code).resolve()
    print(f"[change_project_full_code] new_dir={new_dir}")
    new_dir.mkdir(parents=True, exist_ok=True)

    # ✅ ВАЖНО: old_dir берём из фактического пути ревизии (как показала диагностика)
    any_rev = (
        ProjectRevision.objects
        .select_for_update()
        .filter(project=project)
        .order_by("-created_at")
        .first()
    )
    if not any_rev:
        # нет файлов — можно просто переименовать full_code
        project.full_code = new_full_code
        project.save(update_fields=["full_code"])
        return project

    old_dir = Path(any_rev.file_path).parent.resolve()
    print(f"[change_project_full_code] old_dir(from rev)={old_dir} exists={old_dir.exists()}")

    # безопасность: old_dir должен быть внутри PROJECTS_DIR
    if projects_root not in old_dir.parents:
        raise RuntimeError(f"old_dir вне PROJECTS_DIR: {old_dir}")

    target_project = Project.objects.select_for_update().filter(full_code=new_full_code).first()

    # ---------------------------------------------------------
    # MERGE: такой проект уже есть
    # ---------------------------------------------------------
    if target_project and target_project.pk != project.pk:
        for rev in ProjectRevision.objects.select_for_update().filter(project=project):
            src = Path(rev.file_path).resolve()
            if not src.exists():
                raise RuntimeError(f"Файл ревизии не найден: {src}")

            dst = new_dir / src.name
            if not dst.exists():
                print(f"[change_project_full_code] MOVE src={src} exists={src.exists()} -> dst={dst}")

                shutil.move(str(src), str(dst))

            rev.file_path = str(dst)
            rev.project = target_project
            rev.save(update_fields=["file_path", "project"])

        # удаляем старую папку, если пустая
        try:
            old_dir.rmdir()
        except OSError:
            pass

        project.delete()
        return target_project

    # ---------------------------------------------------------
    # RENAME: просто переименовать проект
    # ---------------------------------------------------------
    for rev in ProjectRevision.objects.select_for_update().filter(project=project):
        src = Path(rev.file_path).resolve()
        if not src.exists():
            raise RuntimeError(f"Файл ревизии не найден: {src}")

        dst = new_dir / src.name
        if not dst.exists():
            print(f"[change_project_full_code] MOVE src={src} exists={src.exists()} -> dst={dst}")

            shutil.move(str(src), str(dst))

        rev.file_path = str(dst)
        rev.save(update_fields=["file_path"])

    # удалить старую папку, если пустая
    try:
        old_dir.rmdir()
    except OSError:
        pass

    project.full_code = new_full_code
    project.save(update_fields=["full_code"])
    return project
