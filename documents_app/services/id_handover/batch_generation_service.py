from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.db import transaction

from documents_app.models import (
    DocumentBatch,
    DocumentBatchGenerationMode,
    DocumentBatchProject,
)
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
    registries_generated_count (int): количество сгенерированных реестров
    letter_generated (bool): было ли сгенерировано письмо
    """
    batch_id: int
    generation_mode: str
    registry_items: list[BatchRegistryGenerationItemResult]
    letter_result: LetterGenerationResult | None
    registries_generated_count: int
    letter_generated: bool


class BatchGenerationServiceError(Exception):
    """Базовая ошибка orchestration-сервиса генерации batch."""


class BatchGenerationValidationError(BatchGenerationServiceError):
    """Ошибка входных данных orchestration-сервиса."""


class BatchGenerationService:
    """
    Оркестратор генерации документов для комплекта (DocumentBatch).

    Задача сервиса:
    - определить, что нужно генерировать по batch.generation_mode
    - соблюсти правильный порядок вызова специализированных сервисов
    - вернуть единый итог генерации

    Правила:
    - REGISTRY_ONLY -> генерируются только project registry
    - LETTER_ONLY   -> генерируется только письмо
    - FULL_SET      -> сначала все project registry, потом письмо

    ВАЖНО:
    - сервис не строит context
    - сервис не рендерит docx/xlsx сам
    - сервис не работает с шаблонами напрямую, кроме проверки путей
    - сервис только оркестрирует вызовы нижележащих сервисов
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
        registry_generation_service: ProjectRegistryGenerationService | None = None,
        letter_generation_service: LetterGenerationService | None = None,
    ) -> None:
        self.registry_generation_service = (
            registry_generation_service or ProjectRegistryGenerationService()
        )
        self.letter_generation_service = (
            letter_generation_service or LetterGenerationService()
        )

    @transaction.atomic
    def generate(
        self,
        *,
        batch: DocumentBatch,
        registry_template_path: str | Path | None = None,
        regular_letter_template_path: str | Path | None = None,
        archive_letter_template_path: str | Path | None = None,
    ) -> BatchGenerationResult:
        """
        Генерирует документы для batch согласно его generation_mode.

        Args:
            batch (DocumentBatch):
                Комплект документов.
            registry_template_path (str | Path | None):
                Путь к XLSX-шаблону реестра.
                Обязателен для режимов REGISTRY_ONLY и FULL_SET.
            regular_letter_template_path (str | Path | None):
                Путь к DOCX-шаблону письма FOR_EXECUTION.
                Обязателен для режимов LETTER_ONLY и FULL_SET.
            archive_letter_template_path (str | Path | None):
                Путь к DOCX-шаблону письма TO_ARCHIVE.
                Обязателен для режимов LETTER_ONLY и FULL_SET.

        Returns:
            BatchGenerationResult
        """
        self._validate_batch(batch=batch)

        registry_items: list[BatchRegistryGenerationItemResult] = []
        letter_result: LetterGenerationResult | None = None

        if self._should_generate_registries(batch=batch):
            registry_template = self._resolve_registry_template_path(
                registry_template_path=registry_template_path,
            )
            batch_projects = self._get_batch_projects(batch=batch)

            for batch_project in batch_projects:
                registry_result = self.registry_generation_service.generate_for_project(
                    batch=batch,
                    project_id=batch_project.project_id,
                    template_path=registry_template,
                )

                registry_items.append(
                    BatchRegistryGenerationItemResult(
                        batch_project_id=batch_project.id,
                        project_id=batch_project.project_id,
                        project_order=batch_project.order,
                        registry_result=registry_result,
                    )
                )

        if self._should_generate_letter(batch=batch):
            regular_template, archive_template = self._resolve_letter_template_paths(
                regular_letter_template_path=regular_letter_template_path,
                archive_letter_template_path=archive_letter_template_path,
            )

            letter_result = self.letter_generation_service.generate_for_batch(
                batch=batch,
                regular_template_path=regular_template,
                archive_template_path=archive_template,
            )

        return BatchGenerationResult(
            batch_id=batch.id,
            generation_mode=batch.generation_mode,
            registry_items=registry_items,
            letter_result=letter_result,
            registries_generated_count=len(registry_items),
            letter_generated=letter_result is not None,
        )

    def _validate_batch(self, *, batch: DocumentBatch) -> None:
        """
        Проверяет базовую корректность batch для запуска генерации.
        """
        if not getattr(batch, "pk", None):
            raise BatchGenerationValidationError(
                "Нельзя запустить генерацию для несохранённого batch."
            )

        if batch.generation_mode not in {
            DocumentBatchGenerationMode.REGISTRY_ONLY,
            DocumentBatchGenerationMode.LETTER_ONLY,
            DocumentBatchGenerationMode.FULL_SET,
        }:
            raise BatchGenerationValidationError(
                f"Неподдерживаемый generation_mode: {batch.generation_mode}"
            )

        has_projects = DocumentBatchProject.objects.filter(batch=batch).exists()
        if not has_projects:
            raise BatchGenerationValidationError(
                "Нельзя запустить генерацию: в комплекте нет проектов."
            )

    def _should_generate_registries(self, *, batch: DocumentBatch) -> bool:
        """
        Нужно ли генерировать project registry для данного batch.
        """
        return batch.generation_mode in self.REGISTRY_GENERATION_MODES

    def _should_generate_letter(self, *, batch: DocumentBatch) -> bool:
        """
        Нужно ли генерировать письмо для данного batch.
        """
        return batch.generation_mode in self.LETTER_GENERATION_MODES

    def _get_batch_projects(self, *, batch: DocumentBatch) -> list[DocumentBatchProject]:
        """
        Возвращает проекты batch в правильном порядке.
        """
        return list(
            DocumentBatchProject.objects
            .filter(batch=batch)
            .select_related("project")
            .order_by("order", "id")
        )

    def _resolve_registry_template_path(
        self,
        *,
        registry_template_path: str | Path | None,
    ) -> Path:
        """
        Проверяет и нормализует путь к XLSX-шаблону реестра.
        """
        if not registry_template_path:
            raise BatchGenerationValidationError(
                "Не передан путь к XLSX-шаблону реестра."
            )

        template_path = Path(registry_template_path)
        if not template_path.exists():
            raise BatchGenerationValidationError(
                f"XLSX-шаблон реестра не найден: {template_path}"
            )

        return template_path

    def _resolve_letter_template_paths(
        self,
        *,
        regular_letter_template_path: str | Path | None,
        archive_letter_template_path: str | Path | None,
    ) -> tuple[Path, Path]:
        """
        Проверяет и нормализует пути к DOCX-шаблонам писем.
        """
        if not regular_letter_template_path:
            raise BatchGenerationValidationError(
                "Не передан путь к DOCX-шаблону письма FOR_EXECUTION."
            )

        if not archive_letter_template_path:
            raise BatchGenerationValidationError(
                "Не передан путь к DOCX-шаблону письма TO_ARCHIVE."
            )

        regular_template = Path(regular_letter_template_path)
        archive_template = Path(archive_letter_template_path)

        if not regular_template.exists():
            raise BatchGenerationValidationError(
                f"DOCX-шаблон письма FOR_EXECUTION не найден: {regular_template}"
            )

        if not archive_template.exists():
            raise BatchGenerationValidationError(
                f"DOCX-шаблон письма TO_ARCHIVE не найден: {archive_template}"
            )

        return regular_template, archive_template