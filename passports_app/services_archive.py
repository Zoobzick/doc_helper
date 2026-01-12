from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from django.core.files import File
from django.core.files.uploadedfile import UploadedFile

from .services import ALLOWED_EXTS, import_single_passport_file


@dataclass(frozen=True)
class ArchiveItemResult:
    rel_path: str
    status: str  # "imported" | "needs_review" | "skipped" | "error"
    message: str
    passport_id: int | None = None


def _normalize_zip_filename(info: zipfile.ZipInfo) -> str:
    """
    Нормализация имени файла из ZIP:
    - Если UTF-8 flag установлен (бит 11) -> имя уже корректное, НЕ трогаем
    - Если флага нет -> zipfile мог декодировать имя как cp437, а реально там cp866/cp1251
    """
    name = info.filename
    UTF8_FLAG = 0x800

    if info.flag_bits & UTF8_FLAG:
        return name

    # zipfile в таких архивах интерпретирует байты имени как cp437 -> получаем "кракозябры".
    # Возьмём исходные байты и попробуем декодировать типичными виндовыми кодировками.
    raw = name.encode("cp437", errors="replace")

    for enc in ("cp866", "cp1251", "utf-8"):
        try:
            fixed = raw.decode(enc)
            if "�" not in fixed:
                return fixed
        except UnicodeDecodeError:
            pass

    return raw.decode("cp1251", errors="replace")


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

    with tempfile.TemporaryDirectory(prefix="passports_zip_") as tmpdir:
        tmpdir_path = Path(tmpdir)

        try:
            zf = zipfile.ZipFile(archive_file)
        except zipfile.BadZipFile:
            raise ValueError("Файл не является корректным ZIP архивом")

        with zf:
            for info in zf.infolist():
                # 1) сначала нормализуем имя из ZIP (иначе кракозябры уже тут)
                normalized_name = _normalize_zip_filename(info)

                # 2) затем применяем ZipSlip-защиту
                safe_name = _safe_relpath(normalized_name)
                if safe_name is None:
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

                out_path = tmpdir_path / safe_name
                out_path.parent.mkdir(parents=True, exist_ok=True)

                try:
                    with zf.open(info, "r") as src, out_path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
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

                try:
                    with out_path.open("rb") as fp:
                        # ВАЖНО: имя передаём уже нормальное (basename от safe_name)
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
