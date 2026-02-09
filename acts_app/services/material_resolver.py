from __future__ import annotations

from acts_app.services.date_format import fmt_date_g


def resolve_material_fields(m):
    """
    Итоговые значения для печати/UI (override-first).

    Возвращает:
      - document_name
      - document_no
      - document_date_str   (строка, уже готовая к печати)
      - material_name
    """
    # --- документ ---
    doc_name = (getattr(m, "manual_doc_name", "") or "").strip()
    doc_no = (getattr(m, "manual_doc_no", "") or "").strip()

    manual_date_text = (getattr(m, "manual_doc_date_text", "") or "").strip()
    manual_date = getattr(m, "manual_doc_date", None)

    # --- материал ---
    material_name = (getattr(m, "manual_name", "") or "").strip()

    # fallback к паспорту
    p = getattr(m, "passport", None) if getattr(m, "passport_id", None) else None

    if p:
        if not doc_name:
            doc_name = (getattr(p, "document_name", "") or "").strip()

        if not doc_no:
            doc_no = (getattr(p, "document_number", "") or "").strip()

        if not material_name and getattr(p, "material", None):
            material_name = (getattr(p.material, "name", "") or "").strip()

    # дата (строкой)
    if manual_date_text:
        doc_date_str = manual_date_text
    else:
        d = manual_date or (getattr(p, "document_date", None) if p else None)
        doc_date_str = fmt_date_g(d) if d else "—"

    return {
        "document_name": doc_name or "Документ",
        "document_no": doc_no,
        "document_date_str": doc_date_str,
        "material_name": material_name or "Материал",
    }
