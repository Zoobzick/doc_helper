from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.db import transaction

from acts_app.models import Act

from documents_app.models import (
    DocumentBatch,
    DocumentBatchAct,
    DocumentBatchGenerationMode,
)
from documents_app.services.id_handover.project_registry_generation_service import (
    ProjectRegistryGenerationResult,
    ProjectRegistryGenerationService,
)


@dataclass(slots=True)
class AffectedBatchProjectRef:
    """
    Ссылка на конкретный project внутри конкретного batch,
    чей реестр нужно пересобрать из-за изменения акта.

    batch_id (id комплекта)
    project_id (id проекта внутри комплекта)
    """
    batch_id: int
    project_id: int


@dataclass(slots=True)
class RebuiltRegistryItemResult:
    """
    Результат пересборки одного проектного реестра.
    """
    batch_id: int
    project_id: int
    generation_result: ProjectRegistryGenerationResult


@dataclass(slots=True)
class RebuildRegistriesForActChangeResult:
    """
    Общий результат оркестратора.

    act_id (какой акт был источником пересборки)
    affected_items (какие batch/project были найдены как затронутые)
    rebuilt_items (что реально было пересобрано)
    skipped_batch_ids (batch, которые содержат акт, но не требуют реестров)
    """
    act_id: int
    affected_items: list[AffectedBatchProjectRef]
    rebuilt_items: list[RebuiltRegistryItemResult]
    skipped_batch_ids: list[int]


class RebuildRegistriesForActChangeServiceError(Exception):
    """Базовая ошибка сервиса пересборки реестров по изменению акта."""


class RebuildRegistriesForActChangeValidationError(RebuildRegistriesForActChangeServiceError):
    """Ошибка входных данных сервиса."""


class RebuildRegistriesForActChangeService:
    """
    Оркестратор пересборки только затронутых XLSX-реестров при изменении одного акта.

    Что делает:
    1. Находит все DocumentBatchAct, где участвует данный Act.
    2. Выделяет уникальные связки (batch_id, project_id).
    3. Отбрасывает batch, где режим генерации не включает реестры.
    4. Для каждой затронутой связки вызывает ProjectRegistryGenerationService.
    5. Не трогает другие проекты и другие batch.

    ВАЖНО:
    - пересборка идёт только для тех project внутри batch, где реально присутствует акт
    - batch с generation_mode=letter_only пропускаются
    - один и тот же act может пересобрать несколько реестров, если он входит в несколько проектов/комплектов
    """

    REGISTRY_RELEVANT_GENERATION_MODES = {
        DocumentBatchGenerationMode.REGISTRY_ONLY,
        DocumentBatchGenerationMode.FULL_SET,
    }

    def __init__(
        self,
        *,
        registry_generation_service: ProjectRegistryGenerationService | None = None,
    ) -> None:
        self.registry_generation_service = (
            registry_generation_service or ProjectRegistryGenerationService()
        )

    @transaction.atomic
    def rebuild_for_act(
        self,
        *,
        act: Act,
        template_path: str | Path,
    ) -> RebuildRegistriesForActChangeResult:
        """
        Пересобирает все затронутые XLSX-реестры для одного акта.

        Args:
            act: изменённый акт
            template_path: путь к xlsx-шаблону реестра

        Returns:
            RebuildRegistriesForActChangeResult
        """
        if not getattr(act, "pk", None):
            raise RebuildRegistriesForActChangeValidationError(
                "Нельзя пересобрать реестры для несохранённого акта."
            )

        affected_rows = self._get_affected_batch_project_rows(act=act)
        affected_items = [
            AffectedBatchProjectRef(
                batch_id=row.batch_id,
                project_id=row.project_id,
            )
            for row in affected_rows
        ]

        rebuilt_items: list[RebuiltRegistryItemResult] = []
        skipped_batch_ids: set[int] = set()

        current_batch_id: int | None = None
        current_batch: DocumentBatch | None = None

        for row in affected_rows:
            if current_batch_id != row.batch_id:
                current_batch_id = row.batch_id
                current_batch = row.batch

            if current_batch is None:
                continue

            if current_batch.generation_mode not in self.REGISTRY_RELEVANT_GENERATION_MODES:
                skipped_batch_ids.add(current_batch.id)
                continue

            generation_result = self.registry_generation_service.generate_for_project(
                batch=current_batch,
                project_id=row.project_id,
                template_path=template_path,
            )

            rebuilt_items.append(
                RebuiltRegistryItemResult(
                    batch_id=current_batch.id,
                    project_id=row.project_id,
                    generation_result=generation_result,
                )
            )

        return RebuildRegistriesForActChangeResult(
            act_id=act.id,
            affected_items=affected_items,
            rebuilt_items=rebuilt_items,
            skipped_batch_ids=sorted(skipped_batch_ids),
        )

    def _get_affected_batch_project_rows(self, *, act: Act) -> list[DocumentBatchAct]:
        """
        Возвращает уникальные связки (batch, project), где участвует данный act.

        ВАЖНО:
        Уникальность нужна на уровне:
        - batch
        - project

        потому что один реестр = один project внутри batch.
        """
        rows = list(
            DocumentBatchAct.objects
            .filter(act=act)
            .select_related("batch", "project")
            .order_by("batch_id", "project_id", "order", "id")
        )

        unique_rows: list[DocumentBatchAct] = []
        seen_pairs: set[tuple[int, int]] = set()

        for row in rows:
            pair = (row.batch_id, row.project_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            unique_rows.append(row)

        return unique_rows