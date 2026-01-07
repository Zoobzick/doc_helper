from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional, Tuple

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Project, ProjectRevision


# =========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================

def normalize_full_code(value: str) -> str:
    """
    Нормализация полного шифра проекта.
    (value) ввод пользователя

    Возвращает:
        нормализованный full_code
    """
    normalized = (value or "").strip()
    normalized = " ".join(normalized.split())
    return normalized


def compute_sha256(file_path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """
    Посчитать sha256 файла.
    """
    p = Path(file_path)
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_int_revision(value: str | None) -> int:
    """
    Преобразует строку ревизии в int.
    '01' -> 1, 'rev02' -> 2, None -> 0
    """
    if not value:
        return 0

    s = str(value).strip()
    if s.isdigit():
        return int(s)

    digits = "".join(ch for ch in s if ch.isdigit())
    return int(digits) if digits else 0


def _format_revision(n: int) -> str:
    """
    1 -> '01', 12 -> '12'
    """
    return f"{n:02d}"


# =========================================================
# РЕВИЗИИ ПРОЕКТА
# =========================================================

@transaction.atomic
def attach_revision_to_project(
        *,
        project: Project,
        file_name: str,
        file_path: str,
        sha256: Optional[str],
) -> Tuple[ProjectRevision, bool]:
    """
    Добавить новую ревизию проекта.

    Правила:
    - revision увеличивается (01, 02, 03...)
    - новая ревизия становится is_latest=True
    - in_production НЕ трогаем
    - дедуп по sha256 внутри проекта

    Возвращает:
        (revision, created)
        created=False если sha256 уже существует у проекта
    """

    # дедуп по sha256 внутри проекта
    if sha256 and ProjectRevision.objects.filter(project=project, sha256=sha256).exists():
        existing = (
            ProjectRevision.objects
            .filter(project=project, sha256=sha256)
            .order_by("-created_at")
            .first()
        )
        return existing, False  # type: ignore[return-value]

    # вычисляем следующую ревизию
    max_rev_str = (
        ProjectRevision.objects
        .filter(project=project)
        .aggregate(m=Max("revision"))
        .get("m")
    )
    next_rev_int = _safe_int_revision(max_rev_str) + 1
    next_rev = _format_revision(next_rev_int)

    # старая актуальная больше не актуальна
    ProjectRevision.objects.filter(project=project, is_latest=True).update(is_latest=False)

    revision = ProjectRevision.objects.create(
        project=project,
        revision=next_rev,
        file_name=file_name,
        file_path=file_path,
        sha256=sha256,
        is_latest=True,
        created_at=timezone.now(),
    )

    return revision, True


# =========================================================
# ПРОИЗВОДСТВО РАБОТ
# =========================================================

@transaction.atomic
def set_revision_in_production(
        *,
        revision_id: int,
        value: bool,
) -> ProjectRevision:
    """
    Установить / снять флаг 'В производство работ' у ревизии.

    ВАЖНО:
    - несколько ревизий одного проекта могут быть in_production=True
    - is_latest НЕ трогаем
    """

    revision = ProjectRevision.objects.select_for_update().get(id=revision_id)
    revision.in_production = bool(value)
    revision.save(update_fields=["in_production"])
    return revision


# =========================================================
# ЧЕРНОВИК → ПОЛНЫЙ ПРОЕКТ / MERGE
# =========================================================

@transaction.atomic
def assign_full_code_to_draft(
        *,
        draft_project_id: int,
        full_code_input: str,
) -> Project:
    """
    Пользователь вводит full_code для черновика.

    Логика:
    1) Если full_code свободен:
       - назначаем его черновику
       - needs_review=False

    2) Если full_code уже существует:
       - переносим ревизии черновика в существующий проект
       - ревизии получают новые номера
       - is_latest корректно пересчитывается
       - in_production СОХРАНЯЕТСЯ
       - черновик удаляется
    """

    normalized = normalize_full_code(full_code_input)
    if not normalized:
        raise ValueError("Полный шифр проекта не может быть пустым")

    draft = Project.objects.select_for_update().get(id=draft_project_id)
    existing = (
        Project.objects
        .select_for_update()
        .filter(full_code=normalized)
        .first()
    )

    # full_code свободен → просто сохраняем
    if existing is None or existing.id == draft.id:
        draft.full_code = normalized
        draft.needs_review = False
        draft.save(update_fields=["full_code", "needs_review"])
        return draft

    # иначе — merge ревизий
    _merge_project_revisions(
        source_project=draft,
        target_project=existing,
    )

    draft.delete()
    return existing


def _merge_project_revisions(
        *,
        source_project: Project,
        target_project: Project,
) -> None:
    """
    Переносит ревизии из source_project в target_project.

    Правила:
    - порядок сохраняется (по created_at)
    - revision перенумеровываются
    - is_latest корректно выставляется
    - in_production сохраняется
    - дедуп по sha256
    """

    target_max_rev_str = (
        ProjectRevision.objects
        .filter(project=target_project)
        .aggregate(m=Max("revision"))
        .get("m")
    )
    next_rev_int = _safe_int_revision(target_max_rev_str)

    source_revisions = (
        ProjectRevision.objects
        .select_for_update()
        .filter(project=source_project)
        .order_by("created_at", "id")
    )

    moved_revisions: list[ProjectRevision] = []

    for rev in source_revisions:
        # дедуп по sha256
        if rev.sha256 and ProjectRevision.objects.filter(
                project=target_project,
                sha256=rev.sha256,
        ).exists():
            continue

        next_rev_int += 1
        rev.project = target_project
        rev.revision = _format_revision(next_rev_int)
        rev.is_latest = False
        rev.save(update_fields=["project", "revision", "is_latest"])

        moved_revisions.append(rev)

    if not moved_revisions:
        return

    # последняя по времени становится актуальной
    ProjectRevision.objects.filter(project=target_project, is_latest=True).update(is_latest=False)
    last_rev = moved_revisions[-1]
    last_rev.is_latest = True
    last_rev.save(update_fields=["is_latest"])


@transaction.atomic
def delete_project_revision(*, revision_id: int) -> None:
    """
    Удаляет ревизию проекта.

    Логика:
    - удаляем PDF файл
    - удаляем запись ProjectRevision
    - если ревизий не осталось → удаляем Project
    - если удалили актуальную → назначаем новую is_latest
    """

    revision = (
        ProjectRevision.objects
        .select_related("project")
        .select_for_update()
        .get(id=revision_id)
    )

    project = revision.project
    was_latest = revision.is_latest

    # удалить файл с диска
    try:
        import os
        if revision.file_path and os.path.exists(revision.file_path):
            os.remove(revision.file_path)
    except Exception:
        pass  # файл не критичен, БД важнее

    revision.delete()

    remaining = (
        ProjectRevision.objects
        .filter(project=project)
        .order_by("-created_at")
    )

    if not remaining.exists():
        # если ревизий больше нет — удаляем проект
        project.delete()
        return

    if was_latest:
        # назначаем новую актуальную
        new_latest = remaining.first()
        new_latest.is_latest = True
        new_latest.save(update_fields=["is_latest"])
