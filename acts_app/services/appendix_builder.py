# acts_app/services/appendix_builder.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from django.db import transaction
from django.db.models import Sum

from acts_app.models import (
    Act,
    ActAppendixLine,
    ActAttachment,
    ActMaterialItem,
    AttachmentType,
)


@dataclass(frozen=True)
class AppendixBuildResult:
    total_lines: int
    total_sheets: int


class AppendixBuilderError(Exception):
    pass


class AppendixBuilder:
    """
    Пересобирает раздел "Приложения" в акте.

    Правила порядка:
    1. Исполнительная схема (ВСЕГДА первая) - обязателен минимум 1 документ.
    2. Материалы (если <5) ИЛИ Реестр материалов (если >=5)
       - листы строки = сумма листов материалов (+ листы реестра материалов, если он используется)
    3. Акт контрольных образцов бетона (если есть)
    4. Протоколы испытаний (если есть)
    5. Прочие документы качества (если есть)

    Примечание:
    - При необходимости можно расширить на approvals отдельным блоком.
    - is_label_overridden=True => label не перезаписываем.
    """

    def __init__(self, act: Act):
        self.act = act

    @transaction.atomic
    def rebuild(self) -> AppendixBuildResult:
        act = self.act

        # собираем источники
        exec_schemes = list(
            act.attachments.filter(type=AttachmentType.EXEC_SCHEME).order_by("created_at")
        )
        if not exec_schemes:
            raise AppendixBuilderError(
                "Нельзя пересобрать приложения: нет ни одной исполнительной схемы (EXEC_SCHEME)."
            )

        # материалы
        materials_count = act.materials.count()
        materials_sheets = self._sum_materials_sheets(act)

        # реестр материалов как документ
        materials_registry = (
            act.attachments.filter(type=AttachmentType.MATERIALS_REGISTRY)
            .order_by("-created_at")
            .first()
        )

        # бетонные образцы
        concrete_samples_act = (
            act.attachments.filter(type=AttachmentType.CONCRETE_SAMPLES_ACT)
            .order_by("-created_at")
            .first()
        )

        # протоколы испытаний (их может быть несколько)
        test_protocols = list(
            act.attachments.filter(type=AttachmentType.TEST_PROTOCOL).order_by("created_at")
        )

        # прочие документы качества (могут быть несколько)
        other_quality_docs = list(
            act.attachments.filter(type=AttachmentType.OTHER_QUALITY_DOC).order_by("created_at")
        )

        # строим "план" приложений
        lines_plan: list[_PlannedLine] = []

        # 1) исполнительная схема - первая
        for i, scheme in enumerate(exec_schemes, start=1):
            # Если схем несколько — они идут подряд в начале
            label = self._format_attachment_label(scheme, default_title="Исполнительная схема")
            lines_plan.append(
                _PlannedLine(
                    kind="ATTACHMENT",
                    label=label,
                    sheets_count=scheme.sheets_count,
                    source_attachment=scheme,
                )
            )

        # 2) материалы/реестр материалов (одной строкой!)
        if materials_count > 0:
            if materials_count >= 5:
                if not materials_registry:
                    raise AppendixBuilderError(
                        "Материалов >= 5, но не добавлен документ 'Реестр материалов' (MATERIALS_REGISTRY). "
                        "Добавь реестр и укажи количество листов."
                    )
                total = materials_sheets + int(materials_registry.sheets_count)
                label = self._format_materials_registry_label(materials_registry)
                lines_plan.append(
                    _PlannedLine(
                        kind="ATTACHMENT",
                        label=label,
                        sheets_count=total,
                        source_attachment=materials_registry,
                    )
                )
            else:
                # Материалы перечислением, без отдельного реестра
                label = "Материалы (паспорта/сертификаты качества)"
                lines_plan.append(
                    _PlannedLine(
                        kind="VIRTUAL",
                        label=label,
                        sheets_count=max(1, materials_sheets),  # на всякий случай
                        source_attachment=None,
                    )
                )

        # 3) акт контрольных образцов бетона (если есть)
        if concrete_samples_act:
            label = self._format_attachment_label(
                concrete_samples_act, default_title="Акт об изготовлении контрольных образцов бетона"
            )
            lines_plan.append(
                _PlannedLine(
                    kind="ATTACHMENT",
                    label=label,
                    sheets_count=concrete_samples_act.sheets_count,
                    source_attachment=concrete_samples_act,
                )
            )

        # 4) протоколы испытаний
        for proto in test_protocols:
            label = self._format_attachment_label(proto, default_title="Протокол испытаний")
            lines_plan.append(
                _PlannedLine(
                    kind="ATTACHMENT",
                    label=label,
                    sheets_count=proto.sheets_count,
                    source_attachment=proto,
                )
            )

        # 5) прочие доки качества
        for doc in other_quality_docs:
            label = self._format_attachment_label(doc, default_title="Документ, подтверждающий качество выполненных работ")
            lines_plan.append(
                _PlannedLine(
                    kind="ATTACHMENT",
                    label=label,
                    sheets_count=doc.sheets_count,
                    source_attachment=doc,
                )
            )

        # применяем план: позиции 1..N
        # сохраняем вручную отредактированные label (is_label_overridden=True)
        existing_by_pos = {
            line.position: line for line in act.appendix_lines.select_for_update().all()
        }

        # удаляем "лишние" строки (которые выходят за новый размер)
        new_size = len(lines_plan)
        for pos, old_line in list(existing_by_pos.items()):
            if pos > new_size:
                old_line.delete()
                existing_by_pos.pop(pos, None)

        # upsert по позициям
        total_sheets = 0
        for pos, planned in enumerate(lines_plan, start=1):
            total_sheets += int(planned.sheets_count)

            existing = existing_by_pos.get(pos)
            if existing:
                # всегда обновляем sheets_count и источник
                existing.sheets_count = int(planned.sheets_count)
                existing.source_attachment = planned.source_attachment

                # label обновляем только если не overridden
                if not existing.is_label_overridden:
                    existing.label = planned.label

                existing.save(update_fields=["sheets_count", "source_attachment", "label", "is_label_overridden"])
            else:
                ActAppendixLine.objects.create(
                    act=act,
                    position=pos,
                    label=planned.label,
                    sheets_count=int(planned.sheets_count),
                    source_attachment=planned.source_attachment,
                    is_label_overridden=False,
                )

        # кешируем сумму листов
        act.sheets_total = int(total_sheets)
        act.save(update_fields=["sheets_total", "updated_at"])

        return AppendixBuildResult(total_lines=new_size, total_sheets=int(total_sheets))

    # -------- helpers --------

    def _sum_materials_sheets(self, act: Act) -> int:
        """
        Сумма листов по всем материалам, добавленным к акту.
        Листы хранятся в ActMaterialItem.sheets_count.
        """
        res = act.materials.aggregate(total=Sum("sheets_count"))
        return int(res["total"] or 0)

    def _format_materials_registry_label(self, registry: ActAttachment) -> str:
        """
        Label для строки "Реестр материалов".
        Важно: sheets_count для этой строки у нас будет СУММА (паспортов + листов реестра).
        """
        # Пишем максимально нейтрально, как обычно в реестрах/приложениях
        parts = ["Реестр материалов"]
        if registry.doc_no:
            parts.append(f"№{registry.doc_no}")
        if registry.doc_date:
            parts.append(f"от {registry.doc_date:%d.%m.%Y}")
        return " ".join(parts)

    def _format_attachment_label(self, att: ActAttachment, default_title: str) -> str:
        """
        Универсальный label для файловых приложений.
        """
        title = (att.title or "").strip() or default_title
        parts = [title]
        if att.doc_no:
            parts.append(f"№{att.doc_no}")
        if att.doc_date:
            parts.append(f"от {att.doc_date:%d.%m.%Y}")
        return " ".join(parts)


@dataclass(frozen=True)
class _PlannedLine:
    kind: str  # ATTACHMENT | VIRTUAL
    label: str
    sheets_count: int
    source_attachment: Optional[ActAttachment]
