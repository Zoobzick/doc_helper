# acts_app/services/act_context.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from django.db.models import Prefetch

from acts_app.models import (
    Act,
    ActAttachment,
    ActMaterialItem,
    AttachmentType,
)
from acts_app.services.appendix_builder import AppendixBuilder, AppendixBuilderError


def _fmt_date(d: Optional[date]) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


@dataclass(frozen=True)
class ActContext:
    """
    Канонический контекст (DTO) для генерации DOCX.
    Здесь НЕ должно быть логики UI. Только “что печатаем”.
    """
    data: dict[str, Any]


class ActContextBuilder:
    """
    Собирает контекст из:
    - Act (сам акт)
    - Act.projects (шифры проектов)
    - Act.materials (материалы/паспорта)
    - Act.attachments (схема + документы качества)
    - Act.appendix_lines (раздел “Приложения”, пересобираем AppendixBuilder-ом)
    """

    def __init__(self, act: Act):
        self.act = act  # act (Act)

    def build(self, *, rebuild_appendix: bool = True) -> ActContext:
        act = self._prefetch(self.act)

        # 1) (опционально) пересобираем “Приложения”, чтобы порядок/листы были консистентны
        appendix_error = ""
        if rebuild_appendix:
            try:
                AppendixBuilder(act).rebuild()
            except AppendixBuilderError as e:
                # не валим генерацию — просто помечаем, что приложения не идеальны
                appendix_error = str(e)

        # 2) projects (шифры проектов)
        project_codes = [p.full_code for p in act.projects.all()]

        # 3) materials
        materials = []
        for mi in act.materials.all():  # mi (ActMaterialItem)
            passport = mi.passport  # passport (Passport|None)
            if passport:
                materials.append(
                    {
                        "position": mi.position,
                        "material_name": passport.material.name if passport.material_id else "",
                        "document_name": passport.document_name,
                        "document_no": passport.document_no,
                        "document_date": passport.document_date,
                        "document_date_str": _fmt_date(passport.document_date),
                        "sheets_count": int(mi.sheets_count),
                        "note": mi.note,
                        "source": "db",
                    }
                )
            else:
                materials.append(
                    {
                        "position": mi.position,
                        "material_name": (mi.manual_name or "").strip(),
                        "document_name": (mi.manual_doc_no or "").strip(),
                        "document_no": "",  # в ручном вводе у тебя поле manual_doc_no, трактуем как имя/№
                        "document_date": mi.manual_doc_date,
                        "document_date_str": _fmt_date(mi.manual_doc_date),
                        "sheets_count": int(mi.sheets_count),
                        "note": mi.note,
                        "source": "manual",
                    }
                )

        # 4) attachments: схема + документы качества (прочие)
        exec_scheme = self._get_exec_scheme_or_fallback(act)
        quality_docs = self._get_quality_docs(act)

        # 5) appendix lines (для печати приложений)
        appendix_lines = []
        for ln in act.appendix_lines.all():
            appendix_lines.append(
                {
                    "position": int(ln.position),
                    "label": ln.label,
                    "sheets_count": int(ln.sheets_count),
                }
            )

        # 6) derived rules (пока просто “флаги” — реализацию реестров добавим позже)
        materials_passports_like_count = len(materials)  # (int) сейчас = кол-во строк материалов
        quality_docs_count_without_exec_scheme = len(quality_docs)

        needs_p3_registry = materials_passports_like_count >= 5
        needs_p4_registry = quality_docs_count_without_exec_scheme >= 5

        data: dict[str, Any] = {
            "act": {
                "uuid": str(act.uuid),
                "number": act.number,
                "act_date": act.act_date,
                "act_date_str": _fmt_date(act.act_date),
                "work_name": act.work_name,
                "work_start_date": act.work_start_date,
                "work_start_date_str": _fmt_date(act.work_start_date),
                "work_end_date": act.work_end_date,
                "work_end_date_str": _fmt_date(act.work_end_date),
                "work_norms_text": act.work_norms_text,
                "allow_next_works_text": act.allow_next_works_text,
                "extra_info_text": act.extra_info_text,
                "copies_count": int(act.copies_count),
                "status": act.status,
            },
            "project_codes": project_codes,
            "materials": materials,
            "quality_docs": {
                "exec_scheme": exec_scheme,  # всегда есть (fallback)
                "docs": quality_docs,        # без EXEC_SCHEME
            },
            "appendix": {
                "lines": appendix_lines,
                "error": appendix_error,
            },
            "rules": {
                "needs_p3_registry": needs_p3_registry,
                "needs_p4_registry": needs_p4_registry,
                "materials_count": materials_passports_like_count,
                "quality_docs_count_without_exec_scheme": quality_docs_count_without_exec_scheme,
            },
        }

        return ActContext(data=data)

    # ----------------- helpers -----------------

    def _prefetch(self, act: Act) -> Act:
        """
        Подтягиваем связанные данные одним заходом.
        """
        return (
            Act.objects
            .filter(pk=act.pk)
            .prefetch_related(
                "projects",
                Prefetch("materials", queryset=ActMaterialItem.objects.select_related("passport", "passport__material")),
                Prefetch("attachments", queryset=ActAttachment.objects.all()),
                "appendix_lines",
            )
            .get()
        )

    def _get_exec_scheme_or_fallback(self, act: Act) -> dict[str, Any]:
        """
        Исполнительная схема:
        - в UI/формсетах у тебя уже гарантируется 1-я строка как EXEC_SCHEME.
        - но на уровне “контекста” мы перестрахуемся:
          если в БД нет EXEC_SCHEME, всё равно делаем “виртуальную” схему.
        """
        scheme = (
            act.attachments
            .filter(type=AttachmentType.EXEC_SCHEME)
            .order_by("created_at")
            .first()
        )

        if scheme:
            return {
                "title": scheme.title or "Исполнительная схема",
                "scheme_name": scheme.doc_no or act.number,  # (scheme_name == № акта)
                "doc_date": scheme.doc_date,
                "doc_date_str": _fmt_date(scheme.doc_date),
                "sheets_count": int(scheme.sheets_count),
                "source": "db",
            }

        # fallback
        return {
            "title": "Исполнительная схема",
            "scheme_name": act.number,
            "doc_date": act.act_date,
            "doc_date_str": _fmt_date(act.act_date),
            "sheets_count": 1,
            "source": "fallback",
        }

    def _get_quality_docs(self, act: Act) -> list[dict[str, Any]]:
        """
        Документы соответствия (кроме исполнительной схемы).
        Пока: считаем все attachments, которые НЕ EXEC_SCHEME и НЕ MATERIALS_REGISTRY.
        (Позже добавим сюда П-4 и отдельные категории.)
        """
        docs = (
            act.attachments
            .exclude(type=AttachmentType.EXEC_SCHEME)
            .exclude(type=AttachmentType.MATERIALS_REGISTRY)
            .order_by("created_at")
        )

        out: list[dict[str, Any]] = []
        for d in docs:
            out.append(
                {
                    "type": d.type,
                    "title": d.title,
                    "doc_no": d.doc_no,
                    "doc_date": d.doc_date,
                    "doc_date_str": _fmt_date(d.doc_date),
                    "sheets_count": int(d.sheets_count),
                }
            )
        return out
