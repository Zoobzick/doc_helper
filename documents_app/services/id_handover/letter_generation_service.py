from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from documents_app.models import (
    DocumentBatch,
    DocumentBatchGenerationMode,
    DocumentBatchLetterType,
    GeneratedDocument,
    GeneratedDocumentSourceKind,
    GeneratedDocumentType,
)
from documents_app.services.id_handover.document_signatures import DocumentSignatureService
from documents_app.services.id_handover.letter_context_builder import LetterContextBuilder
from documents_app.services.id_handover.letter_docx_renderer import LetterDocxRenderer


@dataclass(slots=True)
class LetterGenerationResult:
    """
    Результат генерации письма.
    """
    generated_document: GeneratedDocument
    context: dict
    dependency_signature: str
    created: bool


class LetterGenerationServiceError(Exception):
    """Базовая ошибка сервиса генерации письма."""


class LetterGenerationValidationError(LetterGenerationServiceError):
    """Ошибка входных данных / состава batch для генерации письма."""


class LetterGenerationService:
    """
    Генерирует DOCX-письмо для batch и сохраняет его как GeneratedDocument.

    Что делает:
    1. Проверяет, что режим batch допускает письмо.
    2. Выбирает нужный шаблон по batch.letter_type.
    3. Строит context через LetterContextBuilder.
    4. Рендерит DOCX через LetterDocxRenderer.
    5. Создаёт или обновляет GeneratedDocument типа LETTER_DOCX.

    ВАЖНО:
    - письмо — batch-level документ, поэтому GeneratedDocument.project всегда None
    - pages_count для самого письма не заполняем
    - dependency_signature берётся из DocumentSignatureService
    """

    def __init__(
        self,
        *,
        context_builder: LetterContextBuilder | None = None,
        renderer: LetterDocxRenderer | None = None,
        signature_service: DocumentSignatureService | None = None,
    ) -> None:
        self.context_builder = context_builder or LetterContextBuilder()
        self.renderer = renderer or LetterDocxRenderer()
        self.signature_service = signature_service or DocumentSignatureService()

    @transaction.atomic
    def generate_for_batch(
        self,
        *,
        batch: DocumentBatch,
        regular_template_path: str | Path,
        archive_template_path: str | Path,
    ) -> LetterGenerationResult:
        self._validate_batch_for_letter_generation(batch=batch)

        template_path = self._resolve_template_path(
            batch=batch,
            regular_template_path=regular_template_path,
            archive_template_path=archive_template_path,
        )

        context = self.context_builder.build(batch=batch)

        dependency_signature = self.signature_service.build_batch_letter_signature(
            batch=batch,
            document_type=GeneratedDocumentType.LETTER_DOCX,
        )

        filename = self._build_output_filename(batch=batch)

        generated_document, created = self._get_or_create_generated_document(batch=batch)

        rendered_bytes = self._render_context_to_bytes(
            context=context,
            template_path=template_path,
            batch=batch,
        )

        self._save_file_to_generated_document(
            generated_document=generated_document,
            filename=filename,
            content=rendered_bytes,
        )

        generated_document.source_kind = GeneratedDocumentSourceKind.GENERATED
        generated_document.dependency_signature = dependency_signature
        generated_document.pages_count = None
        generated_document.is_actual = True
        generated_document.created_by = batch.created_by
        generated_document.generated_at = timezone.now()
        generated_document.save()

        return LetterGenerationResult(
            generated_document=generated_document,
            context=context,
            dependency_signature=dependency_signature,
            created=created,
        )

    def _validate_batch_for_letter_generation(self, *, batch: DocumentBatch) -> None:
        if batch.generation_mode not in {
            DocumentBatchGenerationMode.LETTER_ONLY,
            DocumentBatchGenerationMode.FULL_SET,
        }:
            raise LetterGenerationValidationError(
                "Для данного batch генерация письма не предусмотрена текущим generation_mode."
            )

    def _resolve_template_path(
        self,
        *,
        batch: DocumentBatch,
        regular_template_path: str | Path,
        archive_template_path: str | Path,
    ) -> Path:
        if batch.letter_type == DocumentBatchLetterType.FOR_EXECUTION:
            template_path = Path(regular_template_path)
        elif batch.letter_type == DocumentBatchLetterType.TO_ARCHIVE:
            template_path = Path(archive_template_path)
        else:
            raise LetterGenerationValidationError(
                f"Неподдерживаемый тип письма: {batch.letter_type}"
            )

        if not template_path.exists():
            raise LetterGenerationValidationError(
                f"Шаблон письма не найден: {template_path}"
            )

        return template_path

    def _get_or_create_generated_document(
        self,
        *,
        batch: DocumentBatch,
    ) -> tuple[GeneratedDocument, bool]:
        generated_document, created = GeneratedDocument.objects.get_or_create(
            batch=batch,
            project=None,
            document_type=GeneratedDocumentType.LETTER_DOCX,
            defaults={
                "source_kind": GeneratedDocumentSourceKind.GENERATED,
                "created_by": batch.created_by,
                "is_actual": True,
            },
        )
        return generated_document, created

    def _render_context_to_bytes(
        self,
        *,
        context: dict,
        template_path: str | Path,
        batch: DocumentBatch,
    ) -> bytes:
        template_path = Path(template_path)
        if not template_path.exists():
            raise LetterGenerationValidationError(
                f"DOCX-шаблон письма не найден: {template_path}"
            )

        with tempfile.TemporaryDirectory(prefix="doc_helper_letter_") as tmp_dir:
            tmp_dir_path = Path(tmp_dir)
            output_path = tmp_dir_path / f"letter_batch_{batch.id}.docx"

            rendered_path = self.renderer.render_from_context(
                context=context,
                template_path=template_path,
                output_path=output_path,
            )

            return rendered_path.read_bytes()

    def _save_file_to_generated_document(
        self,
        *,
        generated_document: GeneratedDocument,
        filename: str,
        content: bytes,
    ) -> None:
        if generated_document.file:
            try:
                generated_document.file.delete(save=False)
            except Exception:
                pass

        generated_document.file.save(
            filename,
            ContentFile(content),
            save=False,
        )
        generated_document.original_name = filename

    def _build_output_filename(self, *, batch: DocumentBatch) -> str:
        letter_number = self._sanitize_filename_part((batch.letter_number or "").strip())
        if not letter_number:
            letter_number = f"batch_{batch.id}"

        if batch.letter_date:
            letter_date_str = batch.letter_date.strftime("%d.%m.%Y")
        else:
            letter_date_str = "без_даты"

        return f"Письмо №{letter_number} от {letter_date_str}.docx"

    @staticmethod
    def _sanitize_filename_part(value: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        cleaned = "".join("_" if ch in invalid_chars else ch for ch in value)
        cleaned = cleaned.strip().strip(".")
        return cleaned