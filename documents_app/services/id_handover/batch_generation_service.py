from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.db import transaction

from documents_app.models import (
    DocumentBatch,
    DocumentBatchGenerationMode,
    DocumentBatchProject,
    GeneratedDocument,
    GeneratedDocumentType,
)
from documents_app.services.id_handover.document_signatures import DocumentSignatureService
from documents_app.services.id_handover.letter_generation_service import (
    LetterGenerationResult,
    LetterGenerationService,
)
from documents_app.services.id_handover.project_registry_generation_service import (
    ProjectRegistryGenerationResult,
    ProjectRegistryGenerationService,
)


@dataclass(slots=True)
class BatchRegistryGenerationItemResult:
    """
    Результат генерации одного реестра проекта внутри batch.

    batch_project_id (int): id строки DocumentBatchProject
    project_id (int): id проекта
    project_order (int): порядок проекта внутри batch
    registry_result (ProjectRegistryGenerationResult): результат генерации реестра
    """
    batch_project_id: int
    project_id: int
    project_order: int
    registry_result: ProjectRegistryGenerationResult


@dataclass(slots=True)
class BatchGenerationResult:
    """
    Общий результат orchestration-генерации batch.

    batch_id (int): id комплекта
    generation_mode (str): режим генерации комплекта
    registry_items (list[BatchRegistryGenerationItemResult]): результаты по реестрам
    letter_result (LetterGenerationResult | None): результат генерации письма
    registries_generated_count (int): количество реально выполненных генераций реестров
    letter_generated (bool): было ли сгенерировано письмо
    registries_auto_generated_for_letter (bool): были ли реестры догенерированы
        автоматически в режиме LETTER_ONLY
    """
    batch_id: int
    generation_mode: str
    registry_items: list[BatchRegistryGenerationItemResult]
    letter_result: LetterGenerationResult | None
    registries_generated_count: int
    letter_generated: bool
    registries_auto_generated_for_letter: bool


class BatchGenerationServiceError(Exception):
    """Базовая ошибка orchestration-сервиса генерации batch."""


class BatchGenerationValidationError(BatchGenerationServiceError):
    """Ошибка входных данных orchestration-сервиса."""


class BatchGenerationService:
    """
    Оркестратор генерации документов для комплекта (DocumentBatch).

    Цели:
    - определить, что нужно генерировать по batch.generation_mode
    - обеспечить правильный порядок вызова нижележащих сервисов
    - для LETTER_ONLY догенерировать недостающие/неактуальные реестры
    - после генерации обновить актуальность всех generated docs batch

    Правила:
    - REGISTRY_ONLY -> генерируются только project registry
    - LETTER_ONLY   -> сначала при необходимости догенерируются реестры,
                       затем генерируется письмо
    - FULL_SET      -> сначала все project registry, потом письмо
    """

    REGISTRY_GENERATION_MODES = {
        DocumentBatchGenerationMode.REGISTRY_ONLY,
        DocumentBatchGenerationMode.FULL_SET,
    }

    LETTER_GENERATION_MODES = {
        DocumentBatchGenerationMode.LETTER_ONLY,
        DocumentBatchGenerationMode.FULL_SET,
    }

    def __init__(
        self,
        *,
        registry_service: ProjectRegistryGenerationService | None = None,
        letter_service: LetterGenerationService | None = None,
        signature_service: DocumentSignatureService | None = None,
    ) -> None:
        self.registry_service = registry_service or ProjectRegistryGenerationService()
        self.letter_service = letter_service or LetterGenerationService()
        self.signature_service = signature_service or DocumentSignatureService()

    @transaction.atomic
    def generate(
        self,
        *,
        batch: DocumentBatch,
        registry_template_path: str | Path,
        regular_letter_template_path: str | Path,
        archive_letter_template_path: str | Path,
    ) -> BatchGenerationResult:
        self._validate_batch(batch=batch)
        self._validate_required_paths(
            batch=batch,
            registry_template_path=registry_template_path,
            regular_letter_template_path=regular_letter_template_path,
            archive_letter_template_path=archive_letter_template_path,
        )

        batch_projects = self._get_batch_projects(batch=batch)
        registry_items: list[BatchRegistryGenerationItemResult] = []
        letter_result: LetterGenerationResult | None = None
        registries_auto_generated_for_letter = False

        if batch.generation_mode == DocumentBatchGenerationMode.REGISTRY_ONLY:
            registry_items = self._generate_registries_for_batch(
                batch=batch,
                batch_projects=batch_projects,
                registry_template_path=registry_template_path,
            )

        elif batch.generation_mode == DocumentBatchGenerationMode.LETTER_ONLY:
            missing_registry_projects = self._get_projects_requiring_registry_generation_for_letter(
                batch=batch,
                batch_projects=batch_projects,
            )

            if missing_registry_projects:
                registries_auto_generated_for_letter = True
                registry_items = self._generate_registries_for_batch(
                    batch=batch,
                    batch_projects=missing_registry_projects,
                    registry_template_path=registry_template_path,
                )

            letter_result = self._generate_letter_for_batch(
                batch=batch,
                regular_letter_template_path=regular_letter_template_path,
                archive_letter_template_path=archive_letter_template_path,
            )

        elif batch.generation_mode == DocumentBatchGenerationMode.FULL_SET:
            registry_items = self._generate_registries_for_batch(
                batch=batch,
                batch_projects=batch_projects,
                registry_template_path=registry_template_path,
            )

            letter_result = self._generate_letter_for_batch(
                batch=batch,
                regular_letter_template_path=regular_letter_template_path,
                archive_letter_template_path=archive_letter_template_path,
            )

        else:
            raise BatchGenerationValidationError(
                f"Неподдерживаемый режим генерации batch: {batch.generation_mode}"
            )

        self.signature_service.refresh_batch_documents_actuality(batch=batch)

        return BatchGenerationResult(
            batch_id=batch.id,
            generation_mode=batch.generation_mode,
            registry_items=registry_items,
            letter_result=letter_result,
            registries_generated_count=len(registry_items),
            letter_generated=letter_result is not None,
            registries_auto_generated_for_letter=registries_auto_generated_for_letter,
        )

    def _validate_batch(self, *, batch: DocumentBatch) -> None:
        if batch.pk is None:
            raise BatchGenerationValidationError(
                "Нельзя генерировать документы для несохранённого batch."
            )

        if batch.generation_mode not in {
            DocumentBatchGenerationMode.REGISTRY_ONLY,
            DocumentBatchGenerationMode.LETTER_ONLY,
            DocumentBatchGenerationMode.FULL_SET,
        }:
            raise BatchGenerationValidationError(
                f"Некорректный generation_mode у batch id={batch.id}: {batch.generation_mode}"
            )

        if not DocumentBatchProject.objects.filter(batch=batch).exists():
            raise BatchGenerationValidationError(
                "Комплект не содержит ни одного проекта."
            )

    def _validate_required_paths(
        self,
        *,
        batch: DocumentBatch,
        registry_template_path: str | Path,
        regular_letter_template_path: str | Path,
        archive_letter_template_path: str | Path,
    ) -> None:
        if batch.generation_mode in self.REGISTRY_GENERATION_MODES or (
            batch.generation_mode == DocumentBatchGenerationMode.LETTER_ONLY
        ):
            self._ensure_path_exists(
                path=registry_template_path,
                label="XLSX-шаблон реестра",
            )

        if batch.generation_mode in self.LETTER_GENERATION_MODES:
            self._ensure_path_exists(
                path=regular_letter_template_path,
                label="DOCX-шаблон обычного письма",
            )
            self._ensure_path_exists(
                path=archive_letter_template_path,
                label="DOCX-шаблон архивного письма",
            )

    def _ensure_path_exists(self, *, path: str | Path, label: str) -> None:
        path_obj = Path(path)
        if not path_obj.exists():
            raise BatchGenerationValidationError(f"{label} не найден: {path_obj}")

    def _get_batch_projects(self, *, batch: DocumentBatch) -> list[DocumentBatchProject]:
        return list(
            DocumentBatchProject.objects
            .select_related("project")
            .filter(batch=batch)
            .order_by("order", "id")
        )

    def _generate_registries_for_batch(
        self,
        *,
        batch: DocumentBatch,
        batch_projects: list[DocumentBatchProject],
        registry_template_path: str | Path,
    ) -> list[BatchRegistryGenerationItemResult]:
        results: list[BatchRegistryGenerationItemResult] = []

        for batch_project in batch_projects:
            registry_result = self.registry_service.generate_for_project(
                batch=batch,
                project_id=batch_project.project_id,
                template_path=registry_template_path,
            )
            results.append(
                BatchRegistryGenerationItemResult(
                    batch_project_id=batch_project.id,
                    project_id=batch_project.project_id,
                    project_order=batch_project.order,
                    registry_result=registry_result,
                )
            )

        return results

    def _generate_letter_for_batch(
        self,
        *,
        batch: DocumentBatch,
        regular_letter_template_path: str | Path,
        archive_letter_template_path: str | Path,
    ) -> LetterGenerationResult:
        return self.letter_service.generate_for_batch(
            batch=batch,
            regular_template_path=regular_letter_template_path,
            archive_template_path=archive_letter_template_path,
        )

    def _get_projects_requiring_registry_generation_for_letter(
        self,
        *,
        batch: DocumentBatch,
        batch_projects: list[DocumentBatchProject],
    ) -> list[DocumentBatchProject]:
        """
        Для LETTER_ONLY определяет, по каким проектам нужно сначала
        тихо догенерировать реестры, чтобы письмо смогло собраться.

        Реестр считается достаточным для письма, если:
        - существует GeneratedDocument типа REGISTRY_XLSX
        - pages_count > 0
        - dependency_signature совпадает с ожидаемой
        - is_actual == True
        """
        batch_projects_to_generate: list[DocumentBatchProject] = []

        registry_documents_by_project_id = self._get_registry_xlsx_documents_map(batch=batch)

        for batch_project in batch_projects:
            registry_document = registry_documents_by_project_id.get(batch_project.project_id)

            if registry_document is None:
                batch_projects_to_generate.append(batch_project)
                continue

            if not registry_document.pages_count or registry_document.pages_count <= 0:
                batch_projects_to_generate.append(batch_project)
                continue

            if not registry_document.is_actual:
                batch_projects_to_generate.append(batch_project)
                continue

            expected_signature = self.signature_service.build_project_registry_signature(
                batch=batch,
                project_id=batch_project.project_id,
                document_type=GeneratedDocumentType.REGISTRY_XLSX,
            )

            stored_signature = registry_document.dependency_signature or ""
            if stored_signature != expected_signature:
                batch_projects_to_generate.append(batch_project)

        return batch_projects_to_generate

    def _get_registry_xlsx_documents_map(
        self,
        *,
        batch: DocumentBatch,
    ) -> dict[int, GeneratedDocument]:
        documents = (
            GeneratedDocument.objects
            .filter(
                batch=batch,
                document_type=GeneratedDocumentType.REGISTRY_XLSX,
                project__isnull=False,
            )
            .select_related("project")
            .order_by("project_id", "-generated_at", "-id")
        )

        result: dict[int, GeneratedDocument] = {}
        for document in documents:
            if document.project_id not in result:
                result[document.project_id] = document

        return result