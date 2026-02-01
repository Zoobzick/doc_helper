# acts_app/services/appendix_builder.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.db import transaction
from django.db.models import Sum

from acts_app.models import (
    Act,
    ActAppendixLine,
    ActAttachment,
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

    Правила:
    1) Исполнительная схема (ВСЕГДА первая) — минимум 1 документ.
    2) Материалы:
       - если материалов < 5 → одна VIRTUAL-строка "Материалы …" (листов = сумма листов материалов)
       - если материалов >= 5 → создаём/обновляем attachment MATERIALS_REGISTRY и
         в приложениях одна строка-реестр:
         label = "реестр №П-3.<act_number> от <work_end_date>"
         листов = сумма листов материалов + sheets_count самого реестра
    3) Документы, подтверждающие соответствие работ:
       - если (все attachments кроме EXEC_SCHEME и реестров) >= 5 → нужен реестр документов:
         label = "реестр №П-4.<act_number> от <act_date>"
         листов = сумма листов этих документов + sheets_count реестра
       - если < 5 → перечисляем как раньше (бетонные/протоколы/прочие)

    ВАЖНО:
    - title у реестров строго "реестр"
    - для П-3: doc_no="П-3.<act_number>", doc_date=work_end_date
    - для П-4: doc_no="П-4.<act_number>", doc_date=act_date

    is_label_overridden=True => label вручную не перезаписываем.
    """

    # "порог" для появления реестра
    REGISTRY_THRESHOLD = 5

    # название реестра (строго по требованию)
    REGISTRY_TITLE = "реестр"

    def __init__(self, act: Act):
        self.act = act

    @transaction.atomic
    def rebuild(self) -> AppendixBuildResult:
        act = self.act

        # 0) исполнительные схемы — обязательны
        exec_schemes = list(
            act.attachments.filter(type=AttachmentType.EXEC_SCHEME).order_by("created_at")
        )
        if not exec_schemes:
            raise AppendixBuilderError(
                "Нельзя пересобрать приложения: нет ни одной исполнительной схемы (EXEC_SCHEME)."
            )

        # 1) материалы
        materials_count = act.materials.count()
        materials_sheets = self._sum_materials_sheets(act)

        # Автосоздание/обновление реестра материалов (П-3) при >=5
        materials_registry = self._ensure_materials_registry(
            act=act,
            need=(materials_count >= self.REGISTRY_THRESHOLD),
        )

        # 2) документы соответствия (все attachments, кроме схемы и реестров)
        compliance_docs_qs = act.attachments.exclude(
            type__in=self._registry_and_exec_types()
        )
        compliance_docs_count = compliance_docs_qs.count()
        compliance_docs_sheets = self._sum_attachments_sheets(compliance_docs_qs)

        # Автосоздание/обновление реестра документов (П-4) при >=5
        docs_registry = self._ensure_docs_registry(
            act=act,
            need=(compliance_docs_count >= self.REGISTRY_THRESHOLD),
        )

        # 3) если реестр документов включился — то отдельные документы уже "внутри" реестра
        # иначе — показываем как раньше по категориям
        concrete_samples_act = (
            act.attachments.filter(type=AttachmentType.CONCRETE_SAMPLES_ACT)
            .order_by("-created_at")
            .first()
        )
        test_protocols = list(
            act.attachments.filter(type=AttachmentType.TEST_PROTOCOL).order_by("created_at")
        )
        other_quality_docs = list(
            act.attachments.filter(type=AttachmentType.OTHER_QUALITY_DOC).order_by("created_at")
        )

        # --- строим план приложений ---
        lines_plan: list[_PlannedLine] = []

        # 1) Исполнительная схема — первая (если схем несколько — подряд)
        for scheme in exec_schemes:
            label = self._format_attachment_label(scheme, default_title="Исполнительная схема")
            lines_plan.append(
                _PlannedLine(
                    kind="ATTACHMENT",
                    label=label,
                    sheets_count=int(scheme.sheets_count),
                    source_attachment=scheme,
                )
            )

        # 2) Материалы: либо VIRTUAL, либо реестр П-3
        if materials_count > 0:
            if materials_count >= self.REGISTRY_THRESHOLD:
                if not materials_registry:
                    # по логике _ensure_materials_registry сюда не должно попасть
                    raise AppendixBuilderError("Не удалось создать реестр материалов (П-3).")

                total = int(materials_sheets) + int(materials_registry.sheets_count or 0)
                label = self._format_registry_label(materials_registry)
                lines_plan.append(
                    _PlannedLine(
                        kind="ATTACHMENT",
                        label=label,
                        sheets_count=total,
                        source_attachment=materials_registry,
                    )
                )
            else:
                lines_plan.append(
                    _PlannedLine(
                        kind="VIRTUAL",
                        label="Материалы (паспорта/сертификаты качества)",
                        sheets_count=max(1, int(materials_sheets)),
                        source_attachment=None,
                    )
                )

        # 3) Документы соответствия: либо один реестр П-4, либо подробное перечисление как раньше
        if compliance_docs_count > 0:
            if compliance_docs_count >= self.REGISTRY_THRESHOLD:
                if not docs_registry:
                    # если типа нет в AttachmentType — _ensure_docs_registry бросит ошибку,
                    # так что сюда почти не попадём
                    raise AppendixBuilderError("Не удалось создать реестр документов (П-4).")

                total = int(compliance_docs_sheets) + int(docs_registry.sheets_count or 0)
                label = self._format_registry_label(docs_registry)
                lines_plan.append(
                    _PlannedLine(
                        kind="ATTACHMENT",
                        label=label,
                        sheets_count=total,
                        source_attachment=docs_registry,
                    )
                )
            else:
                # как раньше: бетон → протоколы → прочее
                if concrete_samples_act:
                    label = self._format_attachment_label(
                        concrete_samples_act,
                        default_title="Акт об изготовлении контрольных образцов бетона",
                    )
                    lines_plan.append(
                        _PlannedLine(
                            kind="ATTACHMENT",
                            label=label,
                            sheets_count=int(concrete_samples_act.sheets_count),
                            source_attachment=concrete_samples_act,
                        )
                    )

                for proto in test_protocols:
                    label = self._format_attachment_label(proto, default_title="Протокол испытаний")
                    lines_plan.append(
                        _PlannedLine(
                            kind="ATTACHMENT",
                            label=label,
                            sheets_count=int(proto.sheets_count),
                            source_attachment=proto,
                        )
                    )

                for doc in other_quality_docs:
                    label = self._format_attachment_label(
                        doc,
                        default_title="Документ, подтверждающий качество выполненных работ",
                    )
                    lines_plan.append(
                        _PlannedLine(
                            kind="ATTACHMENT",
                            label=label,
                            sheets_count=int(doc.sheets_count),
                            source_attachment=doc,
                        )
                    )

        # --- применяем план: позиции 1..N ---
        existing_by_pos = {
            line.position: line for line in act.appendix_lines.select_for_update().all()
        }

        new_size = len(lines_plan)

        # удалить "лишние" старые строки
        for pos, old_line in list(existing_by_pos.items()):
            if pos > new_size:
                old_line.delete()
                existing_by_pos.pop(pos, None)

        total_sheets = 0
        for pos, planned in enumerate(lines_plan, start=1):
            total_sheets += int(planned.sheets_count)

            existing = existing_by_pos.get(pos)
            if existing:
                existing.sheets_count = int(planned.sheets_count)
                existing.source_attachment = planned.source_attachment

                if not existing.is_label_overridden:
                    existing.label = planned.label

                existing.save(
                    update_fields=[
                        "sheets_count",
                        "source_attachment",
                        "label",
                        "is_label_overridden",
                    ]
                )
            else:
                ActAppendixLine.objects.create(
                    act=act,
                    position=pos,
                    label=planned.label,
                    sheets_count=int(planned.sheets_count),
                    source_attachment=planned.source_attachment,
                    is_label_overridden=False,
                )

        act.sheets_total = int(total_sheets)
        act.save(update_fields=["sheets_total", "updated_at"])

        return AppendixBuildResult(total_lines=new_size, total_sheets=int(total_sheets))

    # ---------------- helpers ----------------

    def _registry_and_exec_types(self) -> list[str]:
        """
        Типы, которые НЕ считаем "документами соответствия":
        - исполнительная схема
        - реестр материалов
        - реестр документов (если появится в AttachmentType)
        """
        types = [
            AttachmentType.EXEC_SCHEME,
            AttachmentType.MATERIALS_REGISTRY,
        ]
        docs_reg = getattr(AttachmentType, "DOCS_REGISTRY", None)
        if docs_reg:
            types.append(docs_reg)
        return types

    def _sum_materials_sheets(self, act: Act) -> int:
        res = act.materials.aggregate(total=Sum("sheets_count"))
        return int(res["total"] or 0)

    def _sum_attachments_sheets(self, qs) -> int:
        res = qs.aggregate(total=Sum("sheets_count"))
        return int(res["total"] or 0)

    def _ensure_materials_registry(self, *, act: Act, need: bool) -> Optional[ActAttachment]:
        """
        Создаёт/обновляет/удаляет реестр материалов (П-3).
        title строго "реестр"
        doc_no: "П-3.<act.number>"
        doc_date: act.work_end_date (если пусто — ошибка, потому что по требованию дата нужна в имени)
        """
        qs = act.attachments.filter(type=AttachmentType.MATERIALS_REGISTRY)

        if not need:
            # если материалов стало меньше порога — реестр не нужен
            qs.delete()
            return None

        if not act.work_end_date:
            raise AppendixBuilderError(
                "Материалов >= 5: нужен реестр материалов (П-3), "
                "но не заполнена 'Дата окончания работ' (work_end_date)."
            )

        obj = qs.order_by("-created_at").first()
        if not obj:
            obj = ActAttachment(act=act, type=AttachmentType.MATERIALS_REGISTRY)

        obj.title = self.REGISTRY_TITLE
        obj.doc_no = f"П-3.{act.number}"
        obj.doc_date = act.work_end_date

        # sheets_count: если пользователь не задавал — ставим 1 (чтобы всегда было валидно)
        obj.sheets_count = int(obj.sheets_count or 1)

        obj.save()
        return obj

    def _ensure_docs_registry(self, *, act: Act, need: bool) -> Optional[ActAttachment]:
        """
        Создаёт/обновляет/удаляет реестр документов соответствия (П-4).
        Требует наличие AttachmentType.DOCS_REGISTRY в models.py + миграцию.
        title строго "реестр"
        doc_no: "П-4.<act.number>"
        doc_date: act.act_date
        """
        docs_reg_type = getattr(AttachmentType, "DOCS_REGISTRY", None)
        if not need:
            if docs_reg_type:
                act.attachments.filter(type=docs_reg_type).delete()
            return None

        if not docs_reg_type:
            raise AppendixBuilderError(
                "Документов соответствия >= 5: нужен реестр документов (П-4), "
                "но в AttachmentType нет значения DOCS_REGISTRY. "
                "Добавь AttachmentType.DOCS_REGISTRY и сделай миграцию."
            )

        qs = act.attachments.filter(type=docs_reg_type)
        obj = qs.order_by("-created_at").first()
        if not obj:
            obj = ActAttachment(act=act, type=docs_reg_type)

        obj.title = self.REGISTRY_TITLE
        obj.doc_no = f"П-4.{act.number}"
        obj.doc_date = act.act_date
        obj.sheets_count = int(obj.sheets_count or 1)

        obj.save()
        return obj

    def _format_registry_label(self, registry: ActAttachment) -> str:
        """
        По требованию: title "реестр"
        label: "реестр №P-3.<act_number> от dd.mm.yyyy"
        """
        return self._format_attachment_label(registry, default_title=self.REGISTRY_TITLE)

    def _format_attachment_label(self, att: ActAttachment, default_title: str) -> str:
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
