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
    - ручное добавление акта в проект batch
    - удаление вручную добавленного акта
    - изменение порядка актов внутри проекта
    - нормализацию порядков внутри проекта

    ВАЖНО:
    - порядок всегда считается только внутри одного проекта
    - auto-акты удалять нельзя
    - act должен быть привязан к project через Act.projects
    """

    @transaction.atomic
    def add_manual_act(self, params: BatchManualActAddParams) -> DocumentBatchAct:
        """
        Ручное добавление акта в конкретный проект комплекта.

        Логика:
        1. Проверяем, что project есть в batch
        2. Проверяем, что act существует
        3. Проверяем, что act привязан к project через M2M Act.projects
        4. Проверяем, что связка (batch, project, act) ещё не существует
        5. Вставляем акт либо в конец, либо на нужную позицию
        6. Сдвигаем order у хвоста вниз
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

        project_items_qs = self._get_project_items_queryset(batch=batch, project_id=project_id)
        items_count = project_items_qs.count()

        if requested_order is None:
            final_order = items_count + 1
        else:
            if requested_order < 1:
                raise DocumentBatchEditingValidationError(
                    "Порядок должен быть числом не меньше 1."
                )
            final_order = min(requested_order, items_count + 1)

        if final_order <= items_count:
            items_to_shift = list(
                project_items_qs.filter(order__gte=final_order).order_by("-order")
            )
            for item in items_to_shift:
                item.order += 1
            DocumentBatchAct.objects.bulk_update(items_to_shift, ["order"])

        batch_act = DocumentBatchAct.objects.create(
            batch=batch,
            project_id=project_id,
            act=act,
            order=final_order,
            source=DocumentBatchActSource.MANUAL,
            added_by=added_by,
        )

        return batch_act

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

        После удаления нормализует порядок внутри проекта.
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

        removed_order = batch_act.order
        batch_act.delete()

        tail_items = list(
            self._get_project_items_queryset(batch=batch, project_id=project_id)
            .filter(order__gt=removed_order)
            .order_by("order", "id")
        )
        for item in tail_items:
            item.order -= 1

        if tail_items:
            DocumentBatchAct.objects.bulk_update(tail_items, ["order"])

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

        batch_act = self._get_batch_act_or_raise(
            batch=batch,
            project_id=project_id,
            batch_act_id=batch_act_id,
        )

        project_items_qs = self._get_project_items_queryset(batch=batch, project_id=project_id)
        items_count = project_items_qs.count()

        if items_count == 0:
            raise DocumentBatchEditingValidationError(
                "В проекте нет актов для перестановки."
            )

        normalized_new_order = min(new_order, items_count)
        old_order = batch_act.order

        if normalized_new_order == old_order:
            return

        if normalized_new_order < old_order:
            affected_items = list(
                project_items_qs.filter(
                    order__gte=normalized_new_order,
                    order__lt=old_order,
                ).exclude(pk=batch_act.pk).order_by("-order")
            )
            for item in affected_items:
                item.order += 1
        else:
            affected_items = list(
                project_items_qs.filter(
                    order__gt=old_order,
                    order__lte=normalized_new_order,
                ).exclude(pk=batch_act.pk).order_by("order")
            )
            for item in affected_items:
                item.order -= 1

        if affected_items:
            DocumentBatchAct.objects.bulk_update(affected_items, ["order"])

        batch_act.order = normalized_new_order
        batch_act.save(update_fields=["order"])

    @transaction.atomic
    def normalize_project_order(
        self,
        *,
        batch: DocumentBatch,
        project_id: int,
    ) -> None:
        """
        Жёстко пересчитывает порядок актов внутри проекта как 1..N.

        Полезно как технический safeguard:
        - после ручных правок
        - после аварийного кейса
        - перед генерацией, если нужно гарантировать плотный порядок
        """
        items = list(
            self._get_project_items_queryset(batch=batch, project_id=project_id)
            .order_by("order", "id")
        )

        changed_items: list[DocumentBatchAct] = []

        for index, item in enumerate(items, start=1):
            if item.order != index:
                item.order = index
                changed_items.append(item)

        if changed_items:
            DocumentBatchAct.objects.bulk_update(changed_items, ["order"])

    def _ensure_project_in_batch(self, *, batch: DocumentBatch, project_id: int) -> None:
        """
        Проверяет, что проект входит в состав batch.
        """
        exists = DocumentBatchProject.objects.filter(
            batch=batch,
            project_id=project_id,
        ).exists()

        if not exists:
            raise DocumentBatchEditingValidationError(
                "Указанный проект не входит в состав данного комплекта."
            )

    def _get_act_for_project(self, *, act_id: int, project_id: int) -> Act:
        """
        Возвращает акт, если он существует и привязан к нужному проекту.
        """
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
        """
        Возвращает DocumentBatchAct внутри конкретного проекта batch.
        """
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
        """
        Базовый queryset строк DocumentBatchAct внутри одного проекта batch.
        """
        return DocumentBatchAct.objects.filter(
            batch=batch,
            project_id=project_id,
        )