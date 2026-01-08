from __future__ import annotations

import errno
import hashlib
import os
import shutil
from pathlib import Path
from typing import Tuple

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from .models import Project, ProjectRevision

from django.utils import timezone

@transaction.atomic
def renumber_project_revisions(project: Project) -> None:
    revisions = list(
        ProjectRevision.objects
        .select_for_update()
        .filter(project=project)
        .order_by("created_at", "id")
    )

    # 1) временные уникальные ревизии (<=10)
    for i, rev in enumerate(revisions, start=1):
        rev.revision = f"T{i:09d}"   # например T000000001
        rev.save(update_fields=["revision"])

    # 2) финальные номера 01..NN
    for i, rev in enumerate(revisions, start=1):
        rev.revision = f"{i:02d}"
        rev.save(update_fields=["revision"])

    # 3) is_latest = последняя
    ProjectRevision.objects.filter(project=project, is_latest=True).update(is_latest=False)
    if revisions:
        last = ProjectRevision.objects.filter(project=project).order_by("-created_at", "-id").first()
        last.is_latest = True
        last.save(update_fields=["is_latest"])


# =========================================================
# STORAGE: ROOT ONLY
# =========================================================

def projects_root() -> Path:
    root = Path(settings.PROJECTS_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_inside_projects_dir(p: Path) -> None:
    root = projects_root()
    p = p.resolve()
    if p != root and root not in p.parents:
        raise RuntimeError(f"Путь вне PROJECTS_DIR: {p}")


def sanitize_filename(value: str) -> str:
    """
    (value) строка для имени файла (full_code)
    Делаем безопасным для FS: убираем слэши, сжимаем пробелы.
    """
    v = (value or "").strip()
    v = " ".join(v.split())
    v = v.replace("/", "_").replace("\\", "_")
    return v


def normalize_full_code(value: str) -> str:
    """
    (value) ввод пользователя
    Нормализация шифра (для хранения в БД).
    """
    value = (value or "").strip()
    value = " ".join(value.split())
    return value


def build_revision_filename(*, full_code: str, revision: str, suffix: str = ".pdf") -> str:
    """
    Итоговое имя файла ревизии в корне PROJECTS_DIR
    """
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return f"{sanitize_filename(full_code)}-{revision}{suffix}"


def _safe_rename(src: Path, dst: Path) -> None:
    """
    (src) откуда
    (dst) куда
    Переименование/перемещение в пределах PROJECTS_DIR.
    Используем os.replace (атомарно).
    """
    safe_inside_projects_dir(src)
    safe_inside_projects_dir(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def ensure_project_files_named(project: Project) -> bool:
    """
    Приводит имена файлов ревизий к формату full_code-REV.pdf в PROJECTS_DIR.
    Ничего никуда не переносим (всё в корне), только rename.

    Возвращает True если что-то переименовали.
    """
    root = projects_root()

    revisions = (
        ProjectRevision.objects
        .select_for_update()
        .filter(project=project)
        .order_by("created_at")
    )

    changed = False

    for rev in revisions:
        src = Path(rev.file_path).resolve()
        safe_inside_projects_dir(src)

        if not src.exists():
            raise RuntimeError(f"Файл ревизии не найден: {src}")

        suffix = src.suffix or ".pdf"
        desired_name = build_revision_filename(full_code=project.full_code or "", revision=rev.revision, suffix=suffix)
        dst = (root / desired_name).resolve()
        safe_inside_projects_dir(dst)

        if src == dst:
            continue

        # не затираем существующий файл
        if dst.exists():
            dst = (root / f"{dst.stem}__dup{suffix}").resolve()
            safe_inside_projects_dir(dst)

        _safe_rename(src, dst)

        rev.file_path = str(dst)
        rev.save(update_fields=["file_path"])
        changed = True

    return changed


# =========================================================
# NEEDS_REVIEW (оставляем как было, но оно больше не влияет на хранение)
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
    for field in REQUIRED_PROJECT_FIELDS:
        val = getattr(project, field)
        if val is None:
            return True
        if isinstance(val, str) and not val.strip():
            return True
    return False


def sync_needs_review(project: Project, *, save: bool = True) -> bool:
    new_val = compute_needs_review(project)
    if project.needs_review != new_val:
        project.needs_review = new_val
        if save:
            project.save(update_fields=["needs_review"])
    return project.needs_review


# =========================================================
# REVISIONS
# =========================================================

def _next_revision_code(project: Project) -> str:
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
        n = 0

    return f"{n + 1:02d}"


@transaction.atomic
def attach_revision_to_project(
    *,
    project: Project,
    file_name: str,
    temp_file_path: str,
    sha256: str | None,
) -> Tuple[ProjectRevision, bool]:
    """
    (temp_file_path) путь на временный файл (из temp upload или из распакованного архива)
    1) считаем номер ревизии
    2) кладём файл в PROJECTS_DIR с именем full_code-REV.pdf
    3) создаём ProjectRevision
    """
    ProjectRevision.objects.filter(project=project, is_latest=True).update(is_latest=False)

    revision_number = _next_revision_code(project)

    src = Path(temp_file_path).resolve()
    if not src.exists():
        raise RuntimeError(f"Временный файл не найден: {src}")

    root = projects_root()
    suffix = src.suffix or ".pdf"
    dst_name = build_revision_filename(full_code=project.full_code or "", revision=revision_number, suffix=suffix)
    dst = (root / dst_name).resolve()
    safe_inside_projects_dir(dst)

    if dst.exists():
        dst = (root / f"{dst.stem}__dup{suffix}").resolve()
        safe_inside_projects_dir(dst)

    # перемещение/rename в пределах диска (если tmp на другом FS, replace может упасть EXDEV)
    try:
        src.replace(dst)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        shutil.copy2(src, dst)
        try:
            src.unlink(missing_ok=True)
        except Exception:
            pass

    revision = ProjectRevision.objects.create(
        project=project,
        revision=revision_number,
        file_name=file_name,
        file_path=str(dst),
        sha256=sha256 or "",
        is_latest=True,
    )

    return revision, True


@transaction.atomic
def set_revision_in_production(*, revision_id: int, value: bool) -> ProjectRevision:
    rev = ProjectRevision.objects.select_for_update().get(id=revision_id)
    rev.in_production = value
    rev.save(update_fields=["in_production"])
    return rev


@transaction.atomic
def delete_project_revision(*, revision_id: int) -> None:
    rev = ProjectRevision.objects.select_related("project").select_for_update().get(id=revision_id)
    project = rev.project
    file_path = Path(rev.file_path)

    rev.delete()

    try:
        file_path.unlink(missing_ok=True)
    except Exception:
        pass

    remaining = ProjectRevision.objects.filter(project=project).order_by("-created_at")
    if not remaining.exists():
        project.delete()
        return

    if not remaining.filter(is_latest=True).exists():
        newest = remaining.first()
        ProjectRevision.objects.filter(project=project, is_latest=True).update(is_latest=False)
        newest.is_latest = True
        newest.save(update_fields=["is_latest"])


# =========================================================
# FULL_CODE CHANGE (rename/merge) + RENAME FILES
# =========================================================

def _make_revisions_temp_unique(project: Project) -> None:
    revs = list(
        ProjectRevision.objects
        .select_for_update()
        .filter(project=project)
        .order_by("created_at", "id")
    )
    for i, rev in enumerate(revs, start=1):
        rev.revision = f"M{i:09d}"   # 10 символов
        rev.save(update_fields=["revision"])

@transaction.atomic
def change_project_full_code(project: Project, new_full_code_input: str) -> Project:
    new_full_code = normalize_full_code(new_full_code_input)
    print(f"[change_project_full_code] project.id={project.id} old='{project.full_code}' new='{new_full_code}'")

    if not new_full_code:
        raise ValueError("new_full_code пустой")

    if project.full_code == new_full_code:
        return project

    target = Project.objects.select_for_update().filter(full_code=new_full_code).first()

    # MERGE: переносим ревизии на существующий проект
    if target and target.pk != project.pk:
        # ✅ ШАГ 1: снять конфликт по (project, revision) ещё ДО смены project_id
        _make_revisions_temp_unique(project)

        # ✅ ШАГ 2: теперь можно переносить ревизии в target без IntegrityError
        ProjectRevision.objects.select_for_update().filter(project=project).update(project=target)

        project.delete()

        sync_needs_review(target, save=True)

        # ✅ ШАГ 3: нормальная нумерация 01..NN и переименование файлов
        renumber_project_revisions(target)
        ensure_project_files_named(target)

        return target

    # RENAME
    project.full_code = new_full_code
    project.save(update_fields=["full_code"])

    sync_needs_review(project, save=True)
    ensure_project_files_named(project)  # ✅ переименовать все ревизии
    return project


# =========================================================
# ASSIGN FULL_CODE TO DRAFT + RENAME FILES
# =========================================================

@transaction.atomic
def assign_full_code_to_draft(*, draft_project_id: int, full_code_input: str) -> Project:
    full_code = normalize_full_code(full_code_input)
    draft = Project.objects.select_for_update().get(id=draft_project_id)

    existing = Project.objects.filter(full_code=full_code).exclude(id=draft.id).first()
    if existing:
        # ✅ снять конфликт по ревизиям у draft
        _make_revisions_temp_unique(draft)

        ProjectRevision.objects.select_for_update().filter(project=draft).update(project=existing)
        draft.delete()

        sync_needs_review(existing, save=True)

        renumber_project_revisions(existing)
        ensure_project_files_named(existing)

        return existing

    draft.full_code = full_code
    draft.save(update_fields=["full_code"])

    sync_needs_review(draft, save=True)
    ensure_project_files_named(draft)
    return draft


# =========================================================
# ZIP: обработка одного PDF (корень + именование)
# =========================================================

@transaction.atomic
def process_single_pdf(*, pdf_path: Path, original_name: str, user) -> dict:
    h = hashlib.sha256()
    with pdf_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    sha256 = h.hexdigest()

    if ProjectRevision.objects.filter(sha256=sha256).exists():
        return {"status": "duplicate", "file": original_name}

    full_code = normalize_full_code(pdf_path.stem)

    project, _ = Project.objects.get_or_create(
        full_code=full_code,
        defaults={"needs_review": True},
    )

    rev, _ = attach_revision_to_project(
        project=project,
        file_name=original_name,
        temp_file_path=str(pdf_path),
        sha256=sha256,
    )

    sync_needs_review(project, save=True)
    # на всякий случай (если full_code поправили) — привести имена
    ensure_project_files_named(project)

    return {"status": "created", "project": project.full_code, "revision": rev.revision, "file": original_name}
