from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional, Iterable, Any

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


_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")  # 08.01.2026


def _parse_first_date_from_text(s: str) -> Optional[date]:
    """
    Берём первую встреченную дату (start-date) из строки:
      "08.01.2026", "08-09.01.2026", "08.01.2026г."
    Нужно только для сортировки.
    """
    if not s:
        return None
    m = _DATE_RE.search(s)
    if not m:
        return None
    dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(yyyy, mm, dd)
    except ValueError:
        return None


def _safe_strip(x: Any) -> str:
    return (str(x).strip() if x is not None else "")


def _strip_trailing_commas(s: str) -> str:
    s = (s or "").strip()
    while s.endswith(","):
        s = s[:-1].rstrip()
    return s


def _join(parts: Iterable[str], sep: str = ", ") -> str:
    out = [p.strip() for p in parts if (p or "").strip()]
    return sep.join(out)


@dataclass(frozen=True)
class _PlannedLine:
    kind: str  # ATTACHMENT | VIRTUAL
    label: str
    sheets_count: int
    source_attachment: Optional[ActAttachment]


class AppendixBuilder:
    """
    Пересобирает раздел "Приложения" в акте.

    Требование (<5 материалов):
    - 1 строка на 1 material_name.
    - Внутри строки: все паспорта этого материала (даже если document_name разный).
    - Порядок: сначала документы/паспорта с более ранней датой.

    Требование (П-4, <5 документов):
    - Все документы в П-4 (кроме исполнительной схемы и реестров) имеют type OTHER_QUALITY_DOC.
    - Склеиваем документы по одинаковому title:
      Title №n1, №n2 от d1, №n3 от d2 (даты по возрастанию).
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
        materials_count = act.materials.count()
        materials_sheets = self._sum_materials_sheets(act)

        materials_registry = self._ensure_materials_registry(
            act=act,
            need=(materials_count >= self.REGISTRY_THRESHOLD),
        )

        # 2) документы соответствия (все attachments, кроме схемы и реестров)
        compliance_docs_qs = act.attachments.exclude(type__in=self._registry_and_exec_types())
        compliance_docs_count = compliance_docs_qs.count()
        compliance_docs_sheets = self._sum_attachments_sheets(compliance_docs_qs)

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

        # ✅ ВАЖНО: все документы П-4 (кроме схем и реестров) — OTHER_QUALITY_DOC
        other_quality_docs = list(
            act.attachments.filter(type=AttachmentType.OTHER_QUALITY_DOC).order_by("created_at", "id")
        )

        # 4) согласования (Доп. сведения)
        approval_items = list(
            act.approval_items.select_related("approval", "approval__project").order_by("position", "id")
        )
        approvals_count = len(approval_items)
        approvals_items_sheets = sum(int(i.sheets_count or 0) for i in approval_items)

        need_approvals_registry = (approvals_count >= self.REGISTRY_THRESHOLD) and (approvals_count > 0)
        approvals_registry = self._ensure_approvals_registry(
            act=act,
            need=need_approvals_registry,
        )

        # --- строим план приложений ---
        lines_plan: list[_PlannedLine] = []

        # 1) Исполнительные схемы
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
                # ✅ <5: 1 строка на 1 material_name, внутри — все паспорта
                for gl in self._build_grouped_material_lines(act):
                    lines_plan.append(gl)

        # 3) Документы соответствия (П-4)
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
                # бетонные образцы — одиночный документ (как было)
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

                # ✅ Склеиваем OTHER_QUALITY_DOC по одинаковому title
                for pl in self._build_grouped_attachment_lines(
                        attachments=other_quality_docs,
                        default_title="Документ",
                ):
                    lines_plan.append(pl)

        # 4) Согласования
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

    # --------- grouping materials (<5) ----------

    def _build_grouped_material_lines(self, act: Act) -> list[_PlannedLine]:
        """
        1 строка на 1 material_name.
        Порядок материалов = порядок position в акте.
        Внутри материала — документы сортируются по дате.
        """

        grouped: dict[str, list[dict[str, Any]]] = {}
        material_order: list[str] = []  # <- сохраняем порядок появления

        for m in act.materials.select_related("passport", "passport__material").order_by("position", "id"):
            data = resolve_material_fields(m)

            material_name = (data.get("material_name") or "").strip() or "Материал"
            document_name = (data.get("document_name") or "").strip() or "Документ"
            document_no = (data.get("document_no") or "").strip()
            document_date_str = (data.get("document_date_str") or "").strip()

            dt = _parse_first_date_from_text(document_date_str) or date.max

            if material_name not in grouped:
                grouped[material_name] = []
                material_order.append(material_name)  # фиксируем порядок

            grouped[material_name].append(
                {
                    "document_name": document_name,
                    "document_no": document_no,
                    "document_date_str": document_date_str,
                    "date": dt,
                    "sheets": int(m.sheets_count or 0),
                }
            )

        out: list[_PlannedLine] = []

        # ❗ больше НЕТ sorted()
        for material_name in material_order:
            group = grouped[material_name]

            doc_map: dict[str, dict[str, list[str]]] = {}
            doc_min_date: dict[str, date] = {}
            sheets_total = 0

            for it in group:
                sheets_total += int(it["sheets"] or 0)

                doc = it["document_name"]
                ds = it["document_date_str"]
                dt = it["date"]

                doc_map.setdefault(doc, {})
                doc_map[doc].setdefault(ds, [])

                if it["document_no"]:
                    doc_map[doc][ds].append(it["document_no"])

                prev = doc_min_date.get(doc)
                if prev is None or dt < prev:
                    doc_min_date[doc] = dt

            # внутри материала документы по дате
            doc_names_sorted = sorted(
                doc_map.keys(),
                key=lambda dn: (doc_min_date.get(dn, date.max), dn),
            )

            chunks: list[str] = []

            for doc_name in doc_names_sorted:
                date_dict = doc_map[doc_name]

                date_keys_sorted = sorted(
                    date_dict.keys(),
                    key=lambda ds: (_parse_first_date_from_text(ds) or date.max, ds),
                )

                first_part = True
                for ds in date_keys_sorted:
                    nos = date_dict.get(ds) or []
                    if not nos:
                        continue

                    nos_part = ", ".join([f"№{n}" for n in nos])
                    part = f"{nos_part} от {ds}" if ds else nos_part

                    if first_part:
                        chunks.append(_strip_trailing_commas(f"{doc_name} {part}"))
                        first_part = False
                    else:
                        chunks.append(_strip_trailing_commas(part))

            docs_text = _join(chunks, sep=", ")
            label = _strip_trailing_commas(f"{docs_text}, {material_name}")

            out.append(
                _PlannedLine(
                    kind="VIRTUAL",
                    label=label,
                    sheets_count=max(1, sheets_total),
                    source_attachment=None,
                )
            )

        return out

    # --------- grouping attachments (P-4) ----------

    def _build_grouped_attachment_lines(
            self,
            *,
            attachments: list[ActAttachment],
            default_title: str,
    ) -> list[_PlannedLine]:
        """
        Склеиваем документы по одинаковому title:
          Title №n1, №n2 от d1, №n3 от d2
        """
        if not attachments:
            return []

        grouped: dict[str, list[ActAttachment]] = {}
        title_order: list[str] = []  # <-- порядок как добавляли (created_at/id)

        for att in attachments:
            title = (att.title or "").strip() or default_title
            if title not in grouped:
                grouped[title] = []
                title_order.append(title)
            grouped[title].append(att)

        out: list[_PlannedLine] = []

        # было: for title in sorted(grouped.keys()):
        for title in title_order:

            group = grouped[title]

            def _key(a: ActAttachment):
                ds = fmt_date_range_g(a.doc_date, getattr(a, "doc_date_to", None))
                dt = _parse_first_date_from_text(ds) or date.max
                return (dt, _safe_strip(a.doc_no))

            group.sort(key=_key)

            by_date: dict[str, list[str]] = {}
            date_order: list[str] = []
            sheets_total = 0

            for a in group:
                sheets_total += int(a.sheets_count or 0)

                ds = fmt_date_range_g(a.doc_date, getattr(a, "doc_date_to", None))
                if ds not in by_date:
                    by_date[ds] = []
                    date_order.append(ds)

                if a.doc_no:
                    by_date[ds].append(_safe_strip(a.doc_no))

            chunks: list[str] = []
            for ds in sorted(date_order, key=lambda x: (_parse_first_date_from_text(x) or date.max, x)):
                nos = by_date.get(ds) or []
                if not nos:
                    continue
                nos_part = ", ".join([f"№{n}" for n in nos])
                chunks.append(_strip_trailing_commas(f"{nos_part} от {ds}" if ds else nos_part))

            label = _strip_trailing_commas(_join([title, _join(chunks, sep=", ")], sep=" "))
            out.append(
                _PlannedLine(
                    kind="ATTACHMENT" if len(group) == 1 else "VIRTUAL",
                    label=label,
                    sheets_count=max(1, sheets_total),
                    source_attachment=group[0] if len(group) == 1 else None,
                )
            )

        return out

    # --------- registries ---------

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

    # --------- formatting ---------

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
