from __future__ import annotations

import os

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from .models import Material, Passport
from .parsers import parse_passport_filename


ALLOWED_EXTS: set[str] = {"pdf", "psd", "xlsx", "docx"}


def _get_ext(filename: str) -> str:
    _, ext = os.path.splitext(filename)
    return (ext or "").lower().lstrip(".")


def _clean(s: str) -> str:
    return " ".join((s or "").strip().split())


@transaction.atomic
def import_single_passport_file(
    *,
    uploaded_file: UploadedFile,
    user,
    material_name: str | None = None,
    document_name: str | None = None,
    document_number: str | None = None,
    document_date=None,  # date | None
) -> Passport:
    """
    Импорт одного файла (веб-вариант):

    Источник данных:
    1) Сначала берём значения из формы (если заполнены пользователем/JS)
    2) Если не заполнены — пытаемся распарсить имя файла
    3) Если и это не удалось — сохраняем как needs_review=True,
       document_name = имя файла без расширения
    """
    original_filename = uploaded_file.name
    ext = _get_ext(original_filename)

    if ext not in ALLOWED_EXTS:
        raise ValueError(f"Неподдерживаемый формат: .{ext}")

    parsed = parse_passport_filename(original_filename)

    # Подготовим значения с приоритетом формы
    material_name = _clean(material_name)
    document_name = _clean(document_name)
    document_number = _clean(document_number)

    if not material_name and parsed:
        material_name = parsed.material
    if not document_name and parsed:
        document_name = parsed.document_name
    if not document_number and parsed:
        document_number = parsed.document_number
    if not document_date and parsed:
        document_date = parsed.document_date

    # needs_review:
    # - если распарсили (или пользователь руками заполнил критичные поля) -> False
    # - иначе True
    has_minimum = bool(document_name)  # минимум: есть имя документа
    fully_parsed_like = bool(material_name and document_name and document_date)

    needs_review = not (fully_parsed_like or (parsed is not None and has_minimum))

    # Материал создаём только если он есть
    material_obj = None
    if material_name:
        material_obj, _ = Material.objects.get_or_create(name=material_name)

    # если совсем ничего не получилось — document_name = имя файла
    if not document_name:
        base, _ = os.path.splitext(original_filename)
        document_name = base
        needs_review = True

    meta = {
        "parser": "v1",
        "status": "ok" if parsed else "failed",
        "from_form": True,
        "material": material_name or "",
        "document_name": document_name or "",
        "document_number": document_number or "",
        "document_date": document_date.isoformat() if document_date else "",
        "original_filename": original_filename,
    }
    if not parsed:
        meta["reason"] = "pattern_mismatch"

    passport = Passport(
        uploaded_by=user,
        material=material_obj,
        document_name=document_name,
        document_number=document_number,
        document_date=document_date,
        needs_review=needs_review,
        parsed_meta=meta,
    )

    passport.file.save(original_filename, uploaded_file, save=False)
    passport.save()
    return passport
