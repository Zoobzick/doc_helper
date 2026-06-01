from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from acts_app.models import Act, AttachmentType
from acts_app.services.act_docx_generator import DocxRenderError, generate_act_docx, get_act_docx_paths
from acts_app.services.registry_p3_docx_generator import generate_and_save_registry_p3_docx, get_registry_p3_docx_paths


@dataclass(frozen=True)
class BulkExportResult:
    content: bytes
    acts_count: int
    files_count: int
    errors: list[str]


def _safe_zip_part(value: str) -> str:
    value = (value or "").strip() or "Без проекта"
    value = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:120] or "Без проекта"


def _project_codes(act: Act) -> list[str]:
    codes: list[str] = []
    for project in act.projects.all().order_by("id"):
        code = (getattr(project, "full_code", "") or "").strip() or str(project).strip()
        if code:
            codes.append(code)
    return codes or ["Без проекта"]


def _existing_or_generated_act_paths(act: Act) -> list[Path]:
    paths = get_act_docx_paths(act)
    if paths and all(path.exists() for path in paths):
        return paths
    return generate_act_docx(act)


def _existing_or_generated_registry_paths(act: Act) -> list[Path]:
    registry = (
        act.attachments
        .filter(type=AttachmentType.MATERIALS_REGISTRY)
        .order_by("-created_at", "-id")
        .first()
    )
    if registry is None:
        return []

    paths = get_registry_p3_docx_paths(act=act, registry=registry)
    if paths and all(path.exists() for path in paths):
        return paths
    return generate_and_save_registry_p3_docx(act=act, registry=registry)


def _unique_archive_name(used_names: set[str], folder_parts: list[str], filename: str) -> str:
    folder = "/".join(_safe_zip_part(part) for part in folder_parts if (part or "").strip())
    base_path = f"{folder}/{filename}" if folder else filename
    if base_path not in used_names:
        used_names.add(base_path)
        return base_path

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 2
    while True:
        candidate_name = f"{stem} ({counter}){suffix}"
        candidate = f"{folder}/{candidate_name}" if folder else candidate_name
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        counter += 1


def _act_folder_name(act_path: Path) -> str:
    return Path(act_path.name).stem


def _write_act_files(
    *,
    archive: zipfile.ZipFile,
    used_names: set[str],
    act: Act,
    act_paths: list[Path],
    registry_paths: list[Path],
    errors: list[str],
) -> int:
    files_count = 0
    project_codes = _project_codes(act)
    has_registry = bool(registry_paths)

    for index, project_code in enumerate(project_codes):
        if not act_paths:
            return files_count

        act_path = act_paths[index] if index < len(act_paths) else act_paths[0]
        if not act_path.exists():
            errors.append(f"Акт {act.number}: файл не найден после генерации: {act_path}")
            continue

        folder_parts = [_safe_zip_part(project_code)]
        if has_registry:
            folder_parts.append(_act_folder_name(act_path))

        archive_name = _unique_archive_name(
            used_names,
            folder_parts,
            _safe_zip_part(act_path.name),
        )
        archive.write(act_path, archive_name)
        files_count += 1

        if not has_registry:
            continue

        registry_path = registry_paths[index] if index < len(registry_paths) else registry_paths[0]
        if not registry_path.exists():
            errors.append(f"Акт {act.number}: реестр материалов не найден после генерации: {registry_path}")
            continue

        archive_name = _unique_archive_name(
            used_names,
            folder_parts,
            _safe_zip_part(registry_path.name),
        )
        archive.write(registry_path, archive_name)
        files_count += 1

    return files_count


def build_acts_bulk_export_zip(acts) -> BulkExportResult:
    buffer = io.BytesIO()
    errors: list[str] = []
    used_names: set[str] = set()
    acts_count = 0
    files_count = 0

    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for act in acts:
            acts_count += 1
            act_paths: list[Path] = []
            registry_paths: list[Path] = []

            try:
                act_paths = _existing_or_generated_act_paths(act)
            except Exception as exc:
                errors.append(f"Акт {act.number}: не удалось добавить акт DOCX: {exc}")

            try:
                registry_paths = _existing_or_generated_registry_paths(act)
            except DocxRenderError as exc:
                errors.append(f"Акт {act.number}: не удалось собрать реестр материалов: {exc}")
            except Exception as exc:
                errors.append(f"Акт {act.number}: ошибка реестра материалов: {exc}")

            files_count += _write_act_files(
                archive=archive,
                used_names=used_names,
                act=act,
                act_paths=act_paths,
                registry_paths=registry_paths,
                errors=errors,
            )

        if errors:
            archive.writestr("Ошибки.txt", "\n".join(errors))

        if acts_count == 0:
            archive.writestr("Ошибки.txt", "За выбранный период акты не найдены.")

    buffer.seek(0)
    return BulkExportResult(
        content=buffer.getvalue(),
        acts_count=acts_count,
        files_count=files_count,
        errors=errors,
    )
