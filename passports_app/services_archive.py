from __future__ import annotations

import os
import zipfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.core.files import File
from django.core.files.uploadedfile import UploadedFile

from .services import import_single_passport_file, ALLOWED_EXTS


@dataclass(frozen=True)
class ArchiveItemResult:
    rel_path: str
    status: str  # "imported" | "needs_review" | "skipped" | "error"
    message: str
    passport_id: int | None = None


def _safe_relpath(name: str) -> str | None:
    """
    Защита от Zip Slip:
    - запрещаем абсолютные пути
    - запрещаем .. (выход из каталога)
    """
    name = name.replace("\\", "/").strip()
    if not name or name.endswith("/"):
        return None
    p = Path(name)
    if p.is_absolute():
        return None
    if ".." in p.parts:
        return None
    return str(p)


def _ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower().lstrip(".")


def import_passports_from_zip(*, archive_file: UploadedFile, user) -> tuple[dict, list[ArchiveItemResult]]:
    """
    Импорт из ZIP:
    - читаем архив
    - берём все файлы (включая вложенные папки)
    - фильтруем по расширению (pdf/psd/xlsx/docx)
    - каждый файл импортируем как одиночный паспорт
    Возвращаем (stats, results)
    """
    results: list[ArchiveItemResult] = []
    stats = {"total": 0, "imported": 0, "needs_review": 0, "skipped": 0, "errors": 0}

    # Временная папка для файлов, извлечённых из архива
    with tempfile.TemporaryDirectory(prefix="passports_zip_") as tmpdir:
        tmpdir_path = Path(tmpdir)

        try:
            zf = zipfile.ZipFile(archive_file)
        except zipfile.BadZipFile:
            raise ValueError("Файл не является корректным ZIP архивом")

        with zf:
            for info in zf.infolist():
                safe_name = _safe_relpath(info.filename)
                if safe_name is None:
                    # директория или небезопасный путь
                    continue

                stats["total"] += 1

                ext = _ext(safe_name)
                if ext not in ALLOWED_EXTS:
                    stats["skipped"] += 1
                    results.append(
                        ArchiveItemResult(
                            rel_path=safe_name,
                            status="skipped",
                            message=f"Пропущен: неподдерживаемое расширение .{ext}",
                        )
                    )
                    continue

                # Извлекаем файл во временную папку
                out_path = tmpdir_path / safe_name
                out_path.parent.mkdir(parents=True, exist_ok=True)

                try:
                    with zf.open(info, "r") as src, open(out_path, "wb") as dst:
                        dst.write(src.read())
                except Exception as e:
                    stats["errors"] += 1
                    results.append(
                        ArchiveItemResult(
                            rel_path=safe_name,
                            status="error",
                            message=f"Ошибка извлечения: {e}",
                        )
                    )
                    continue

                # Импортируем как обычный файл
                try:
                    with open(out_path, "rb") as fp:
                        django_file = File(fp, name=os.path.basename(safe_name))
                        passport = import_single_passport_file(uploaded_file=django_file, user=user)

                    if passport.needs_review:
                        stats["needs_review"] += 1
                        results.append(
                            ArchiveItemResult(
                                rel_path=safe_name,
                                status="needs_review",
                                message="Импортировано, но требует проверки",
                                passport_id=passport.id,
                            )
                        )
                    else:
                        stats["imported"] += 1
                        results.append(
                            ArchiveItemResult(
                                rel_path=safe_name,
                                status="imported",
                                message="Импортировано успешно",
                                passport_id=passport.id,
                            )
                        )

                except Exception as e:
                    stats["errors"] += 1
                    results.append(
                        ArchiveItemResult(
                            rel_path=safe_name,
                            status="error",
                            message=f"Ошибка импорта: {e}",
                        )
                    )

    return stats, results
