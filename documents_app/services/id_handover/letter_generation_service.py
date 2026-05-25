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
from documents_app.utils.pdf_utils import convert_docx_to_pdf, get_pdf_pages_count


@dataclass(slots=True)
class LetterGenerationResult:
    """
    Результат генерации письма.

    docx_document (GeneratedDocument): основной DOCX письма
    pdf_document (GeneratedDocument): preview PDF письма
    context (dict): контекст, по которому строилось письмо
    dependency_signature (str): сигнатура зависимостей для DOCX/PDF
    docx_created (bool): был ли создан новый GeneratedDocument DOCX
    pdf_created (bool): был ли создан новый GeneratedDocument PDF
    pages_count (int): число страниц preview PDF
    """
    docx_document: GeneratedDocument
    pdf_document: GeneratedDocument
    context: dict
    dependency_signature: str
    docx_created: bool
    pdf_created: bool
    pages_count: int


class LetterGenerationServiceError(Exception):
    """Базовая ошибка сервиса генерации письма."""


class LetterGenerationValidationError(LetterGenerationServiceError):
    """Ошибка входных данных / состава batch для генерации письма."""


class LetterGenerationService:
    """
    Генерирует письмо для batch и сохраняет:

    - LETTER_DOCX
    - LETTER_PREVIEW_PDF

    ВАЖНО:
    - письмо — batch-level документ, поэтому project всегда None
    - DOCX и PDF получают одну и ту же dependency_signature
    - pages_count берётся по итоговому preview PDF
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

        docx_dependency_signature = self.signature_service.build_batch_letter_signature(
            batch=batch,
            document_type=GeneratedDocumentType.LETTER_DOCX,
        )
        pdf_dependency_signature = self.signature_service.build_batch_letter_signature(
            batch=batch,
            document_type=GeneratedDocumentType.LETTER_PREVIEW_PDF,
        )

        docx_filename = self._build_docx_output_filename(batch=batch)
        pdf_filename = self._build_pdf_output_filename(batch=batch)

        docx_document, docx_created = self._get_or_create_generated_document(
            batch=batch,
            document_type=GeneratedDocumentType.LETTER_DOCX,
        )
        pdf_document, pdf_created = self._get_or_create_generated_document(
            batch=batch,
            document_type=GeneratedDocumentType.LETTER_PREVIEW_PDF,
        )

        rendered_docx_bytes, rendered_pdf_bytes, pages_count = self._render_context_to_files(
            context=context,
            template_path=template_path,
            batch=batch,
        )

        self._save_file_to_generated_document(
            generated_document=docx_document,
            filename=docx_filename,
            content=rendered_docx_bytes,
        )
        self._save_file_to_generated_document(
            generated_document=pdf_document,
            filename=pdf_filename,
            content=rendered_pdf_bytes,
        )

        generated_at = timezone.now()

        self._apply_generated_document_meta(
            generated_document=docx_document,
            batch=batch,
            dependency_signature=docx_dependency_signature,
            pages_count=pages_count,
            generated_at=generated_at,
        )
        self._apply_generated_document_meta(
            generated_document=pdf_document,
            batch=batch,
            dependency_signature=pdf_dependency_signature,
            pages_count=pages_count,
            generated_at=generated_at,
        )

        return LetterGenerationResult(
            docx_document=docx_document,
            pdf_document=pdf_document,
            context=context,
            dependency_signature=docx_dependency_signature,
            docx_created=docx_created,
            pdf_created=pdf_created,
            pages_count=pages_count,
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
        document_type: str,
    ) -> tuple[GeneratedDocument, bool]:
        generated_document, created = GeneratedDocument.objects.get_or_create(
            batch=batch,
            project=None,
            document_type=document_type,
            defaults={
                "source_kind": GeneratedDocumentSourceKind.GENERATED,
                "created_by": batch.created_by,
                "is_actual": True,
            },
        )
        return generated_document, created

    def _render_context_to_files(
        self,
        *,
        context: dict,
        template_path: str | Path,
        batch: DocumentBatch,
    ) -> tuple[bytes, bytes, int]:
        template_path = Path(template_path)
        if not template_path.exists():
            raise LetterGenerationValidationError(
                f"DOCX-шаблон письма не найден: {template_path}"
            )

        with tempfile.TemporaryDirectory(prefix="doc_helper_letter_") as tmp_dir:
            tmp_dir_path = Path(tmp_dir)
            docx_output_path = tmp_dir_path / f"letter_batch_{batch.id}.docx"

            rendered_docx_path = self.renderer.render_from_context(
                context=context,
                template_path=template_path,
                output_path=docx_output_path,
            )

            pdf_path = convert_docx_to_pdf(
                docx_path=rendered_docx_path,
                output_dir=tmp_dir_path,
            )
            pages_count = get_pdf_pages_count(pdf_path)

            return (
                rendered_docx_path.read_bytes(),
                pdf_path.read_bytes(),
                pages_count,
            )

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

    def _apply_generated_document_meta(
        self,
        *,
        generated_document: GeneratedDocument,
        batch: DocumentBatch,
        dependency_signature: str,
        pages_count: int,
        generated_at,
    ) -> None:
        generated_document.source_kind = GeneratedDocumentSourceKind.GENERATED
        generated_document.dependency_signature = dependency_signature
        generated_document.pages_count = pages_count
        generated_document.is_actual = True
        generated_document.created_by = batch.created_by
        generated_document.generated_at = generated_at
        generated_document.save()

    def _build_docx_output_filename(self, *, batch: DocumentBatch) -> str:
        letter_number = self._sanitize_filename_part((batch.letter_number or "").strip())
        if not letter_number:
            letter_number = f"batch_{batch.id}"

        if batch.letter_date:
            letter_date_str = batch.letter_date.strftime("%d.%m.%Y")
        else:
            letter_date_str = "без_даты"

        batch_title = self._sanitize_filename_part((batch.title or "").strip())
        title_part = f" ({batch_title})" if batch_title else ""
        return f"Письмо №{letter_number} от {letter_date_str}{title_part}.docx"

    def _build_pdf_output_filename(self, *, batch: DocumentBatch) -> str:
        letter_number = self._sanitize_filename_part((batch.letter_number or "").strip())
        if not letter_number:
            letter_number = f"batch_{batch.id}"

        if batch.letter_date:
            letter_date_str = batch.letter_date.strftime("%d.%m.%Y")
        else:
            letter_date_str = "без_даты"

        batch_title = self._sanitize_filename_part((batch.title or "").strip())
        title_part = f" ({batch_title})" if batch_title else ""
        return f"Письмо №{letter_number} от {letter_date_str}{title_part}.pdf"

    @staticmethod
    def _sanitize_filename_part(value: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        cleaned = "".join("_" if ch in invalid_chars else ch for ch in value)
        cleaned = cleaned.strip().strip(".")
        return cleaned
