from acts_app.services.date_format import fmt_date_g

def resolve_material_fields(m):
    """
    Возвращает финальные значения для печати/UI
    """
    # --- документ ---
    doc_name = (m.note or "").strip()
    doc_no = (m.manual_doc_no or "").strip()
    doc_date = m.manual_doc_date

    # --- материал ---
    material_name = (m.manual_name or "").strip()

    if m.passport_id and m.passport:
        p = m.passport

        if not doc_name:
            doc_name = (p.document_name or "").strip()

        if not doc_no:
            doc_no = (p.document_number or "").strip()

        if not doc_date:
            doc_date = p.document_date

        if not material_name and p.material:
            material_name = (p.material.name or "").strip()

    return {
        "document_name": doc_name or "Документ",
        "document_no": doc_no,
        "document_date_str": fmt_date_g(doc_date) if doc_date else "—",
        "material_name": material_name or "Материал",
    }
