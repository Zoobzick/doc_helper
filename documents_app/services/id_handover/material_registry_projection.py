from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from acts_app.models import ActAppendixLine, AttachmentType
from acts_app.services.material_resolver import resolve_material_fields


def _normalize_spaces(value: str) -> str:
    value = (value or "").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def _normalize_document_number(value: str) -> str:
    value = _normalize_spaces(value).lower()
    while value.startswith("№"):
        value = value[1:].strip()
    return value


def _build_document_label(*, document_name: str, document_no: str, document_date: str) -> str:
    parts = [document_name]
    if document_no:
        parts.append(f"№{document_no}")
    if document_date and document_date != "—":
        parts.append(f"от {document_date}")
    return " ".join(part for part in parts if part).strip()


@dataclass(frozen=True, slots=True)
class MaterialRegistryDocument:
    """Один физический документ в раскрытом составе реестра материалов batch."""

    label: str
    sheets_count: int
    is_manual: bool
    material_names: tuple[str, ...]
    source_item_ids: tuple[int, ...]

    def to_signature_dict(self) -> dict:
        return {
            "label": self.label,
            "sheets_count": self.sheets_count,
            "is_manual": self.is_manual,
            "material_names": list(self.material_names),
            "source_item_ids": list(self.source_item_ids),
        }


def build_material_registry_documents(materials: Iterable) -> list[MaterialRegistryDocument]:
    """
    Строит batches-проекцию паспортов материалов.

    Паспорта из БД объединяются по реквизитам документа, как в реестре П-3.
    Ручные строки никогда не объединяются, даже если их текст полностью совпадает.
    """
    result: list[MaterialRegistryDocument | None] = []
    db_group_indexes: dict[tuple[str, str, str], int] = {}
    db_groups: dict[int, dict] = {}

    for material in materials:
        data = resolve_material_fields(material)
        document_name = _normalize_spaces(data.get("document_name") or "Документ") or "Документ"
        document_no = _normalize_spaces(data.get("document_no") or "")
        document_date = _normalize_spaces(data.get("document_date_str") or "—") or "—"
        material_name = _normalize_spaces(data.get("material_name") or "Материал") or "Материал"
        sheets_count = max(int(getattr(material, "sheets_count", 0) or 0), 1)
        item_id = int(getattr(material, "id", 0) or 0)

        base_label = _build_document_label(
            document_name=document_name,
            document_no=document_no,
            document_date=document_date,
        )

        if not getattr(material, "passport_id", None):
            result.append(
                MaterialRegistryDocument(
                    label=f"{base_label}, {material_name}".strip().strip(","),
                    sheets_count=sheets_count,
                    is_manual=True,
                    material_names=(material_name,),
                    source_item_ids=(item_id,),
                )
            )
            continue

        document_key = (
            document_name.lower(),
            _normalize_document_number(document_no),
            document_date.lower(),
        )
        result_index = db_group_indexes.get(document_key)
        if result_index is None:
            result_index = len(result)
            db_group_indexes[document_key] = result_index
            db_groups[result_index] = {
                "base_label": base_label,
                "sheets_count": sheets_count,
                "material_names": [material_name],
                "material_name_keys": {material_name.lower()},
                "source_item_ids": [item_id],
            }
            result.append(None)
            continue

        group = db_groups[result_index]
        group["sheets_count"] = max(group["sheets_count"], sheets_count)
        material_name_key = material_name.lower()
        if material_name_key not in group["material_name_keys"]:
            group["material_name_keys"].add(material_name_key)
            group["material_names"].append(material_name)
        group["source_item_ids"].append(item_id)

    for result_index, group in db_groups.items():
        material_names = tuple(group["material_names"])
        result[result_index] = MaterialRegistryDocument(
            label=f"{group['base_label']}, {'; '.join(material_names)}".strip().strip(","),
            sheets_count=group["sheets_count"],
            is_manual=False,
            material_names=material_names,
            source_item_ids=tuple(group["source_item_ids"]),
        )

    return [document for document in result if document is not None]


def build_act_material_registry_documents(act) -> list[MaterialRegistryDocument]:
    return build_material_registry_documents(act.materials.all())


def get_batch_appendix_sheets_count(appendix_line: ActAppendixLine) -> int:
    """Возвращает количество листов строки приложения именно для реестра batch."""
    stored_count = max(int(appendix_line.sheets_count or 0), 1)
    attachment = getattr(appendix_line, "source_attachment", None)
    if getattr(attachment, "type", None) != AttachmentType.MATERIALS_REGISTRY:
        return stored_count

    documents = build_act_material_registry_documents(appendix_line.act)
    if not documents:
        return stored_count

    registry_sheets = max(int(getattr(attachment, "sheets_count", 0) or 0), 1)
    return registry_sheets + sum(document.sheets_count for document in documents)
