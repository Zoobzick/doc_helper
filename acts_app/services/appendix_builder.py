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
from acts_app.services.date_format import fmt_date_range_g
from acts_app.services.material_resolver import resolve_material_fields


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
       - если материалов < 5 → расписываем каждый материал отдельной строкой (VIRTUAL)
       - если материалов >= 5 → создаём/обновляем attachment MATERIALS_REGISTRY и
         в приложениях одна строка-реестр:
         label = "реестр №П-3.<act_number> от <work_end_date>"
         листов = сумма листов материалов + sheets_count самого реестра
    3) Документы, подтверждающие соответствие работ:
       - если (все attachments кроме EXEC_SCHEME и реестров) >= 5 → нужен реестр документов:
         label = "реестр №П-4.<act_number> от <act_date>"
         листов = сумма листов этих документов + sheets_count реестра
       - если < 5 → перечисляем как раньше (бетонные/протоколы/прочие)

    4) Согласования (Доп. сведения):
       - источник данных: ActApprovalItem (act.approval_items)
       - если согласований >= 5 → создаём/обновляем attachment APPROVALS_REGISTRY (П-8),
         и в приложениях показываем одну строку-реестр.
         листов = сумма листов согласований + sheets_count реестра П-8
       - если 1..4 → показываем поочерёдно каждое согласование отдельной строкой (VIRTUAL),
         листов = item.sheets_count, текст = item.label_override (если пусто — fallback из Approval).
       - если 0 → ничего не показываем и удаляем реестр П-8, если он был создан ранее.

    ВАЖНО:
    - title у реестров строго "реестр"
    - для П-3: doc_no="П-3.<act_number>", doc_date=work_end_date
    - для П-4: doc_no="П-4.<act_number>", doc_date=act_date
    - для П-8: doc_no="П-8.<act_number>", doc_date=act_date

    is_label_overridden=True => label вручную не перезаписываем.
    """

    REGISTRY_THRESHOLD = 5
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
        materials_count = act.materials.count()  # (materials_count) кол-во материалов в акте
        materials_sheets = self._sum_materials_sheets(act)  # (materials_sheets) сумма sheets_count по материалам

        materials_registry = self._ensure_materials_registry(
            act=act,
            need=(materials_count >= self.REGISTRY_THRESHOLD),
        )

        # 2) документы соответствия (все attachments, кроме схемы и реестров)
        compliance_docs_qs = act.attachments.exclude(type__in=self._registry_and_exec_types())
        compliance_docs_count = compliance_docs_qs.count()  # (compliance_docs_count) кол-во документов
        compliance_docs_sheets = self._sum_attachments_sheets(compliance_docs_qs)  # (compliance_docs_sheets) сумма листов

        docs_registry = self._ensure_docs_registry(
            act=act,
            need=(compliance_docs_count >= self.REGISTRY_THRESHOLD),
        )

        # 3) категории документов (для режима < 5)
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

        # 4) согласования (Доп. сведения) — источник ActApprovalItem
        approval_items = list(
            act.approval_items.select_related("approval", "approval__project").order_by("position", "id")
        )
        approvals_count = len(approval_items)  # (approvals_count) кол-во согласований
        approvals_items_sheets = sum(int(i.sheets_count or 0) for i in approval_items)  # сумма листов по согласованиям

        need_approvals_registry = (approvals_count >= self.REGISTRY_THRESHOLD) and (approvals_count > 0)
        approvals_registry = self._ensure_approvals_registry(
            act=act,
            need=need_approvals_registry,
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

        # 2) Материалы
        if materials_count > 0:
            if materials_count >= self.REGISTRY_THRESHOLD:
                if not materials_registry:
                    raise AppendixBuilderError("Не удалось создать реестр материалов (П-3).")

                total = int(materials_sheets) + int(materials_registry.sheets_count or 0)
                label = self._format_registry_label(materials_registry)

                lines_plan.append(
                    _PlannedLine(
                        kind="ATTACHMENT",
                        label=label,
                        sheets_count=max(1, total),
                        source_attachment=materials_registry,
                    )
                )
            else:
                # <5: каждый материал отдельной строкой (VIRTUAL)
                for m in act.materials.select_related("passport", "passport__material").order_by("position", "id"):
                    label = self._format_material_label(m)
                    lines_plan.append(
                        _PlannedLine(
                            kind="VIRTUAL",
                            label=label,
                            sheets_count=max(1, int(m.sheets_count or 0)),
                            source_attachment=None,
                        )
                    )

        # 3) Документы соответствия
        if compliance_docs_count > 0:
            if compliance_docs_count >= self.REGISTRY_THRESHOLD:
                if not docs_registry:
                    raise AppendixBuilderError("Не удалось создать реестр документов (П-4).")

                total = int(compliance_docs_sheets) + int(docs_registry.sheets_count or 0)
                label = self._format_registry_label(docs_registry)
                lines_plan.append(
                    _PlannedLine(
                        kind="ATTACHMENT",
                        label=label,
                        sheets_count=max(1, total),
                        source_attachment=docs_registry,
                    )
                )
            else:
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

        # 4) Согласования — в конец
        if approvals_count > 0:
            if approvals_count >= self.REGISTRY_THRESHOLD:
                if not approvals_registry:
                    raise AppendixBuilderError("Не удалось создать реестр согласований (П-8).")

                total = int(approvals_items_sheets) + int(approvals_registry.sheets_count or 0)
                label = self._format_registry_label(approvals_registry)
                lines_plan.append(
                    _PlannedLine(
                        kind="ATTACHMENT",
                        label=label,
                        sheets_count=max(1, total),
                        source_attachment=approvals_registry,
                    )
                )
            else:
                def _lower_first(s: str) -> str:
                    s = (s or "").strip()
                    if not s:
                        return ""
                    return s[:1].lower() + s[1:]

                for item in approval_items:
                    label = (item.label_override or "").strip()
                    if not label:
                        label = (item.approval.description or "").strip()
                    if not label:
                        label = "согласование"
                    label = _lower_first(label)

                    lines_plan.append(
                        _PlannedLine(
                            kind="VIRTUAL",
                            label=label,
                            sheets_count=max(1, int(item.sheets_count or 0)),
                            source_attachment=None,
                        )
                    )
        else:
            self._ensure_approvals_registry(act=act, need=False)

        # --- применяем план ---
        existing_by_pos = {
            line.position: line for line in act.appendix_lines.select_for_update().all()
        }

        new_size = len(lines_plan)

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
        types = [
            AttachmentType.EXEC_SCHEME,
            AttachmentType.MATERIALS_REGISTRY,
        ]

        docs_reg = getattr(AttachmentType, "DOCS_REGISTRY", None)
        if docs_reg:
            types.append(docs_reg)

        approvals_reg = getattr(AttachmentType, "APPROVALS_REGISTRY", None)
        if approvals_reg:
            types.append(approvals_reg)

        return types

    def _sum_materials_sheets(self, act: Act) -> int:
        res = act.materials.aggregate(total=Sum("sheets_count"))
        return int(res["total"] or 0)

    def _sum_attachments_sheets(self, qs) -> int:
        res = qs.aggregate(total=Sum("sheets_count"))
        return int(res["total"] or 0)

    def _ensure_materials_registry(self, *, act: Act, need: bool) -> Optional[ActAttachment]:
        qs = act.attachments.filter(type=AttachmentType.MATERIALS_REGISTRY)

        if not need:
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
        obj.sheets_count = int(obj.sheets_count or 1)

        obj.save()
        return obj

    def _ensure_docs_registry(self, *, act: Act, need: bool) -> Optional[ActAttachment]:
        docs_reg_type = getattr(AttachmentType, "DOCS_REGISTRY", None)
        if not need:
            if docs_reg_type:
                act.attachments.filter(type=docs_reg_type).delete()
            return None

        if not docs_reg_type:
            raise AppendixBuilderError(
                "Документов соответствия >= 5: нужен реестр документов (П-4), "
                "но в AttachmentType нет значения DOCS_REGISTRY."
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

    def _ensure_approvals_registry(self, *, act: Act, need: bool) -> Optional[ActAttachment]:
        approvals_reg_type = getattr(AttachmentType, "APPROVALS_REGISTRY", None)

        if not approvals_reg_type:
            if need:
                raise AppendixBuilderError(
                    "Согласований >= 5: нужен реестр согласований (П-8), "
                    "но в AttachmentType нет значения APPROVALS_REGISTRY."
                )
            return None

        qs = act.attachments.filter(type=approvals_reg_type)

        if not need:
            qs.delete()
            return None

        obj = qs.order_by("-created_at").first()
        if not obj:
            obj = ActAttachment(act=act, type=approvals_reg_type)

        obj.title = self.REGISTRY_TITLE
        obj.doc_no = f"П-8.{act.number}"
        obj.doc_date = act.act_date
        obj.sheets_count = int(obj.sheets_count or 1)

        obj.save()
        return obj

    def _format_registry_label(self, registry: ActAttachment) -> str:
        return self._format_attachment_label(registry, default_title=self.REGISTRY_TITLE)

    def _format_attachment_label(self, att: ActAttachment, default_title: str) -> str:
        title = (att.title or "").strip() or default_title
        parts = [title]

        if att.doc_no:
            parts.append(f"№{att.doc_no}")

        date_str = fmt_date_range_g(att.doc_date, getattr(att, "doc_date_to", None))
        if date_str:
            parts.append(f"от {date_str}")

        return " ".join(parts)

    def _format_material_label(self, m) -> str:
        """
        Формат для материалов (<5):
        document_name №document_no от document_date, material

        Источник истины: resolve_material_fields(m)
        """
        data = resolve_material_fields(m)  # (data) итоговые поля с учётом override

        parts = [data["document_name"]]

        if data["document_no"]:
            parts.append(f"№{data['document_no']}")

        if data["document_date_str"] and data["document_date_str"] != "—":
            parts.append(f"от {data['document_date_str']}")

        base = " ".join(parts).strip()
        material_name = (data["material_name"] or "").strip()

        return f"{base}, {material_name}".strip().strip(",")


@dataclass(frozen=True)
class _PlannedLine:
    kind: str  # ATTACHMENT | VIRTUAL
    label: str
    sheets_count: int
    source_attachment: Optional[ActAttachment]
