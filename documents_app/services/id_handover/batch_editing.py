from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction

from acts_app.models import Act

from documents_app.models import (
    DocumentBatch,
    DocumentBatchAct,
    DocumentBatchActSource,
    DocumentBatchProject,
)

User = get_user_model()


@dataclass(slots=True)
class BatchManualActAddParams:
    """
    Параметры ручного добавления акта в комплект.

    batch (комплект)
    project_id (id проекта внутри batch, в реестр которого добавляем акт)
    act_id (id акта)
    added_by (пользователь, выполняющий действие)
    order (необязательная позиция внутри проекта; если не передана — акт добавляется в конец)
    """
    batch: DocumentBatch
    project_id: int
    act_id: int
    added_by: User
    order: int | None = None


@dataclass(slots=True)
class BatchActMoveParams:
    """
    Параметры смены порядка акта внутри проекта.

    batch (комплект)
    project_id (id проекта внутри batch)
    batch_act_id (id DocumentBatchAct, который двигаем)
    new_order (новая позиция внутри проекта, начиная с 1)
    """
    batch: DocumentBatch
    project_id: int
    batch_act_id: int
    new_order: int


class DocumentBatchEditingError(Exception):
    """Базовая ошибка редактирования состава комплекта."""


class DocumentBatchEditingValidationError(DocumentBatchEditingError):
    """Ошибка валидации действий пользователя."""


class DocumentBatchEditingService:
    """
    Сервис редактирования состава комплекта сдачи ИД.

    Поддерживает:
    - ручное добавление акта в уже существующий проект batch
    - удаление акта из текущего состава проекта batch
    - изменение порядка актов внутри проекта
    - нормализацию порядков внутри проекта

    ВАЖНО:
    - порядок актов считается только внутри одного проекта
    - act должен быть привязан к project через Act.projects
    - вручную можно добавить акт вне периода, но только в проект,
      который уже входит в batch
    """

    TEMP_ORDER_GAP = 10000

    @transaction.atomic
    def add_manual_act(self, params: BatchManualActAddParams) -> DocumentBatchAct:
        """
        Ручное добавление акта в конкретный проект комплекта.

        Логика:
        1. Проверяем, что project уже входит в batch
        2. Проверяем, что act существует и относится к project
        3. Проверяем, что связка (batch, project, act) ещё не существует
        4. Создаём новый DocumentBatchAct
        5. Полностью безопасно перестраиваем порядок внутри проекта

        ВАЖНО:
        - здесь НЕТ ограничения по периоду акта
        - если акт относится к проекту batch, его можно добавить вручную
        """
        batch = params.batch
        project_id = params.project_id
        act_id = params.act_id
        added_by = params.added_by
        requested_order = params.order

        self._ensure_project_in_batch(batch=batch, project_id=project_id)

        act = self._get_act_for_project(act_id=act_id, project_id=project_id)

        already_exists = DocumentBatchAct.objects.filter(
            batch=batch,
            project_id=project_id,
            act=act,
        ).exists()
        if already_exists:
            raise DocumentBatchEditingValidationError(
                "Этот акт уже добавлен в указанный проект данного комплекта."
            )

        existing_items = list(
            self._get_project_items_queryset(batch=batch, project_id=project_id)
            .select_related("act", "project", "batch")
            .order_by("order", "id")
        )

        items_count = len(existing_items)

        if requested_order is None:
            final_order = items_count + 1
        else:
            if requested_order < 1:
                raise DocumentBatchEditingValidationError(
                    "Порядок должен быть числом не меньше 1."
                )
            final_order = min(requested_order, items_count + 1)

        temp_order = self._get_safe_temp_order(items_count=items_count)

        batch_act = DocumentBatchAct.objects.create(
            batch=batch,
            project_id=project_id,
            act=act,
            order=temp_order,
            source=DocumentBatchActSource.MANUAL,
            added_by=added_by,
        )

        reordered_items = existing_items[:]
        insert_index = final_order - 1
        reordered_items.insert(insert_index, batch_act)

        self._apply_dense_order(
            batch=batch,
            project_id=project_id,
            ordered_items=reordered_items,
        )

        batch_act.refresh_from_db(fields=["order"])
        return batch_act

    @transaction.atomic
    def remove_act(
        self,
        *,
        batch: DocumentBatch,
        project_id: int,
        batch_act_id: int,
    ) -> None:
        """
        Удаляет любой акт из текущего состава проекта batch.

        Это удаление строки DocumentBatchAct из комплекта,
        а НЕ удаление самого акта из acts_app.
        """
        batch_act = self._get_batch_act_or_raise(
            batch=batch,
            project_id=project_id,
            batch_act_id=batch_act_id,
        )

        batch_act.delete()

        remaining_items = list(
            self._get_project_items_queryset(batch=batch, project_id=project_id)
            .select_related("act", "project", "batch")
            .order_by("order", "id")
        )

        self._apply_dense_order(
            batch=batch,
            project_id=project_id,
            ordered_items=remaining_items,
        )

    @transaction.atomic
    def remove_manual_act(
        self,
        *,
        batch: DocumentBatch,
        project_id: int,
        batch_act_id: int,
    ) -> None:
        """
        Удаляет только вручную добавленный акт из проекта batch.

        Оставлен для обратной совместимости.
        """
        batch_act = self._get_batch_act_or_raise(
            batch=batch,
            project_id=project_id,
            batch_act_id=batch_act_id,
        )

        if batch_act.source != DocumentBatchActSource.MANUAL:
            raise DocumentBatchEditingValidationError(
                "Можно удалять только акты, добавленные вручную."
            )

        self.remove_act(
            batch=batch,
            project_id=project_id,
            batch_act_id=batch_act_id,
        )

    @transaction.atomic
    def move_act_within_project(self, params: BatchActMoveParams) -> None:
        """
        Перемещает акт на новую позицию внутри одного проекта batch.

        Пример:
        было: 1,2,3,4,5
        двигаем позицию 5 -> 2
        станет: 1,5,2,3,4
        """
        batch = params.batch
        project_id = params.project_id
        batch_act_id = params.batch_act_id
        new_order = params.new_order

        if new_order < 1:
            raise DocumentBatchEditingValidationError(
                "Новая позиция должна быть числом не меньше 1."
            )

        self._ensure_project_in_batch(batch=batch, project_id=project_id)

        batch_act = self._get_batch_act_or_raise(
            batch=batch,
            project_id=project_id,
            batch_act_id=batch_act_id,
        )

        project_items = list(
            self._get_project_items_queryset(batch=batch, project_id=project_id)
            .select_related("act", "project", "batch")
            .order_by("order", "id")
        )

        items_count = len(project_items)

        if items_count == 0:
            raise DocumentBatchEditingValidationError(
                "В проекте нет актов для перестановки."
            )

        normalized_new_order = min(new_order, items_count)
        old_index = None

        for index, item in enumerate(project_items):
            if item.id == batch_act.id:
                old_index = index
                break

        if old_index is None:
            raise DocumentBatchEditingValidationError(
                "Не найден акт для перемещения внутри проекта."
            )

        new_index = normalized_new_order - 1

        if new_index == old_index:
            return

        moving_item = project_items.pop(old_index)
        project_items.insert(new_index, moving_item)

        self._apply_dense_order(
            batch=batch,
            project_id=project_id,
            ordered_items=project_items,
        )

    @transaction.atomic
    def normalize_project_order(
        self,
        *,
        batch: DocumentBatch,
        project_id: int,
    ) -> None:
        """
        Жёстко пересчитывает порядок актов внутри проекта как 1..N.
        """
        items = list(
            self._get_project_items_queryset(batch=batch, project_id=project_id)
            .select_related("act", "project", "batch")
            .order_by("order", "id")
        )

        self._apply_dense_order(
            batch=batch,
            project_id=project_id,
            ordered_items=items,
        )

    def _apply_dense_order(
        self,
        *,
        batch: DocumentBatch,
        project_id: int,
        ordered_items: list[DocumentBatchAct],
    ) -> None:
        """
        Безопасно выставляет плотный порядок 1..N.

        В 2 фазы:
        1. Сначала уводим все order во временную безопасную зону
        2. Затем ставим финальные значения 1..N
        """
        if not ordered_items:
            return

        temp_base = self._get_safe_temp_order(items_count=len(ordered_items))

        temp_changed_items: list[DocumentBatchAct] = []
        for index, item in enumerate(ordered_items, start=1):
            temp_order = temp_base + index
            if item.order != temp_order:
                item.order = temp_order
                temp_changed_items.append(item)

        if temp_changed_items:
            DocumentBatchAct.objects.bulk_update(temp_changed_items, ["order"])

        final_changed_items: list[DocumentBatchAct] = []
        for index, item in enumerate(ordered_items, start=1):
            if item.order != index:
                item.order = index
                final_changed_items.append(item)

        if final_changed_items:
            DocumentBatchAct.objects.bulk_update(final_changed_items, ["order"])

    def _get_safe_temp_order(self, *, items_count: int) -> int:
        return self.TEMP_ORDER_GAP + items_count

    def _ensure_project_in_batch(self, *, batch: DocumentBatch, project_id: int) -> None:
        exists = DocumentBatchProject.objects.filter(
            batch=batch,
            project_id=project_id,
        ).exists()

        if not exists:
            raise DocumentBatchEditingValidationError(
                "Указанный проект не входит в состав данного комплекта."
            )

    def _get_act_for_project(self, *, act_id: int, project_id: int) -> Act:
        act = (
            Act.objects.filter(id=act_id, projects__id=project_id)
            .distinct()
            .first()
        )
        if not act:
            raise DocumentBatchEditingValidationError(
                "Указанный акт не найден или не относится к выбранному проекту."
            )
        return act

    def _get_batch_act_or_raise(
        self,
        *,
        batch: DocumentBatch,
        project_id: int,
        batch_act_id: int,
    ) -> DocumentBatchAct:
        batch_act = (
            DocumentBatchAct.objects.filter(
                id=batch_act_id,
                batch=batch,
                project_id=project_id,
            )
            .select_related("act", "project", "batch")
            .first()
        )
        if not batch_act:
            raise DocumentBatchEditingValidationError(
                "Не найден акт комплекта для указанного проекта."
            )
        return batch_act

    def _get_project_items_queryset(
        self,
        *,
        batch: DocumentBatch,
        project_id: int,
    ):
        return DocumentBatchAct.objects.filter(
            batch=batch,
            project_id=project_id,
        )