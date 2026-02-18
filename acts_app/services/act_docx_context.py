from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from django.db.models import Q

from acts_app.models import Act, AttachmentType
from acts_app.services.date_format import fmt_date_g, fmt_date_range_g
from acts_app.services.material_resolver import resolve_material_fields
from directive_app.models import ActRole, Authorization
from orgs_app.models import PersonNRS, SroKind

_MONTHS_RU_GENITIVE = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


# -------------------------
# text helpers
# -------------------------

def _safe_str(x: Any) -> str:
    return (str(x).strip() if x is not None else "")


def _join_non_empty(parts: Iterable[str], sep: str = ", ") -> str:
    items = [p.strip() for p in parts if (p or "").strip()]
    return sep.join(items)


def _strip_trailing_commas(s: str) -> str:
    s = (s or "").strip()
    while s.endswith(","):
        s = s[:-1].rstrip()
    return s


def _normalize_fio_short(s: str) -> str:
    """
    'Бибичев А. А.' -> 'Бибичев А.А.'
    """
    s = (s or "").strip()
    s = re.sub(r"([А-ЯЁ])\.\s+([А-ЯЁ])\.", r"\1.\2.", s)
    return s


def _date_parts_for_template(d) -> dict[str, str]:
    """
    В шаблоне: “ {{dd}} ” {{mm}} 20 {{yy}} г.
    => mm = 'октября', yy = '25'
    """
    if not d:
        return {"dd": "", "mm": "", "yy": ""}
    return {
        "dd": f"{d.day:02d}",
        "mm": _MONTHS_RU_GENITIVE.get(int(d.month), ""),
        "yy": f"{d.year % 100:02d}",
    }


def _get_first_attr(obj: Any, names: tuple[str, ...], default: str = "") -> str:
    if obj is None:
        return default
    for name in names:
        if hasattr(obj, name):
            val = getattr(obj, name, None)
            if val is None:
                continue
            s = str(val).strip()
            if s:
                return s
    return default


def _full_name_or_str(val: Any) -> str:
    if val is None:
        return ""
    full_name = getattr(val, "full_name", None)
    if isinstance(full_name, str) and full_name.strip():
        return full_name.strip()
    return str(val).strip()


def _project_attr_full_name(project: Any, *, direct_attr_names: tuple[str, ...], related_attr: str) -> str:
    if project is None:
        return ""

    for name in direct_attr_names:
        if hasattr(project, name):
            v = getattr(project, name, None)
            s = _full_name_or_str(v)
            if s:
                related_obj = getattr(project, related_attr, None)
                related_full = _full_name_or_str(related_obj)
                if related_full:
                    return related_full
                return s

    related_obj = getattr(project, related_attr, None)
    related_full = _full_name_or_str(related_obj)
    if related_full:
        return related_full

    return ""


# -------------------------
# Projects
# -------------------------

def build_projects_text(act: Act) -> dict[str, str]:
    qs = act.projects.all().order_by("id")
    first = qs.first()

    codes: list[str] = []
    for p in qs:
        code = _get_first_attr(p, ("full_code", "code", "cipher", "number", "name", "title"), default=str(p))
        if code:
            codes.append(code)

    project_line = _project_attr_full_name(
        first,
        direct_attr_names=("project_line", "construction_line", "object_line", "line"),
        related_attr="line",
    )

    project_stage = _project_attr_full_name(
        first,
        direct_attr_names=("project_stage", "stage", "design_stage"),
        related_attr="stage",
    )

    project_address = _get_first_attr(first, ("project_address", "address", "addr", "location"), default="")

    return {
        "projects": ", ".join([c for c in codes if c.strip()]),
        "project_line": project_line,
        "project_stage": project_stage,
        "project_address": project_address,
    }


# -------------------------
# Materials (п.3)
# -------------------------

@dataclass(frozen=True)
class _MaterialRow:
    material_name: str
    document_name: str
    document_no: str
    document_date_str: str


def _collect_material_rows(act: Act) -> list[_MaterialRow]:
    rows: list[_MaterialRow] = []
    for m in act.materials.select_related("passport", "passport__material").order_by("position", "id"):
        data = resolve_material_fields(m)
        rows.append(
            _MaterialRow(
                material_name=(data["material_name"] or "Материал").strip(),
                document_name=(data["document_name"] or "Документ").strip(),
                document_no=(data["document_no"] or "—").strip(),
                document_date_str=(data["document_date_str"] or "—").strip(),
            )
        )
    return rows


# для сортировки дат вида "05.12.2025г." / "05-06.12.2025г."
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def _parse_first_date_from_text(s: str):
    if not s:
        return None
    m = _DATE_RE.search(s)
    if not m:
        return None
    dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        from datetime import date as _d
        return _d(yyyy, mm, dd)
    except Exception:
        return None


def build_passports_text(act: Act) -> str:
    count = act.materials.count()
    if count <= 0:
        return ""

    if count >= 5:
        if act.work_end_date:
            return f"реестр №П-3.{act.number} от {fmt_date_g(act.work_end_date)}"
        return f"реестр №П-3.{act.number}"

    rows = _collect_material_rows(act)

    material_names = [r.material_name for r in rows]
    all_materials_unique = (len(set(material_names)) == len(material_names))

    same_material = (len(set(r.material_name for r in rows)) == 1)
    same_doc_name = (len(set(r.document_name for r in rows)) == 1)
    same_doc_date = (len(set(r.document_date_str for r in rows)) == 1)

    if all_materials_unique:
        return _strip_trailing_commas(", ".join(
            f"{r.material_name} ({r.document_name} №{r.document_no} от {r.document_date_str})"
            for r in rows
        ))

    if same_material and same_doc_name and same_doc_date:
        mname = rows[0].material_name
        dname = rows[0].document_name
        ddate = rows[0].document_date_str
        nums = ", ".join([f"№{r.document_no}" for r in rows])
        return _strip_trailing_commas(f"{mname} ({dname} {nums} от {ddate})")

    if same_material:
        mname = rows[0].material_name

        doc_map: dict[str, dict[str, list[str]]] = {}
        doc_min_date = {}

        for r in rows:
            doc = (r.document_name or "Документ").strip()
            ds = (r.document_date_str or "—").strip()
            no = (r.document_no or "—").strip()

            doc_map.setdefault(doc, {})
            doc_map[doc].setdefault(ds, [])
            if no and no != "—":
                doc_map[doc][ds].append(no)

            dt = _parse_first_date_from_text(ds)
            if dt is not None:
                prev = doc_min_date.get(doc)
                if prev is None or dt < prev:
                    doc_min_date[doc] = dt

        def _doc_key(dn: str):
            from datetime import date as _d
            return (doc_min_date.get(dn, _d.max), dn)

        inner_parts: list[str] = []
        for doc_name in sorted(doc_map.keys(), key=_doc_key):
            date_dict = doc_map[doc_name]

            def _date_key(ds: str):
                from datetime import date as _d
                return (_parse_first_date_from_text(ds) or _d.max, ds)

            first = True
            for ds in sorted(date_dict.keys(), key=_date_key):
                nos = date_dict.get(ds) or []
                if not nos:
                    continue

                nos_part = ", ".join([f"№{n}" for n in nos])

                if first:
                    inner_parts.append(f"{doc_name} {nos_part} от {ds}")
                    first = False
                else:
                    inner_parts.append(f"{nos_part} от {ds}")

        inner = _strip_trailing_commas(", ".join(inner_parts))
        return _strip_trailing_commas(f"{mname} ({inner})")

    return _strip_trailing_commas(", ".join(
        f"{r.material_name} ({r.document_name} №{r.document_no} от {r.document_date_str})"
        for r in rows
    ))


# -------------------------
# Docs (п.4)  ✅ FIX HERE
# -------------------------

def _format_attachment(att) -> str:
    title = (getattr(att, "title", "") or "").strip() or "Документ"
    doc_no = (getattr(att, "doc_no", "") or "").strip()

    doc_date_from = getattr(att, "doc_date", None)
    doc_date_to = getattr(att, "doc_date_to", None)

    parts = [title]
    if doc_no:
        parts.append(f"№{doc_no}")

    date_str = fmt_date_range_g(doc_date_from, doc_date_to)
    if date_str:
        parts.append(f"от {date_str}")

    return _strip_trailing_commas(" ".join(parts))


def _format_attachments_grouped_by_title(attachments: list) -> str:
    """
    Делает склейку, как в "Приложении":
    title №n1 от d1, №n2 от d2
    (title печатаем один раз, далее только номера и даты)
    """
    if not attachments:
        return ""

    # title -> [attachments]
    grouped: dict[str, list] = {}
    title_min_date = {}

    for a in attachments:
        title = (getattr(a, "title", "") or "").strip() or "Документ"
        grouped.setdefault(title, []).append(a)

        ds = fmt_date_range_g(getattr(a, "doc_date", None), getattr(a, "doc_date_to", None))
        dt = _parse_first_date_from_text(ds)
        if dt is not None:
            prev = title_min_date.get(title)
            if prev is None or dt < prev:
                title_min_date[title] = dt

    def _title_key(t: str):
        from datetime import date as _d
        return (title_min_date.get(t, _d.max), t)

    parts: list[str] = []

    for title in sorted(grouped.keys(), key=_title_key):
        items = grouped[title]

        # date_str -> [nos]
        by_date: dict[str, list[str]] = {}
        date_order: list[str] = []

        for a in items:
            ds = fmt_date_range_g(getattr(a, "doc_date", None), getattr(a, "doc_date_to", None))
            if ds not in by_date:
                by_date[ds] = []
                date_order.append(ds)

            no = (getattr(a, "doc_no", "") or "").strip()
            if no:
                by_date[ds].append(no)

        def _date_key(ds: str):
            from datetime import date as _d
            return (_parse_first_date_from_text(ds) or _d.max, ds)

        first = True
        for ds in sorted(date_order, key=_date_key):
            nos = by_date.get(ds) or []
            if not nos:
                continue

            nos_part = ", ".join([f"№{n}" for n in nos])

            if first:
                parts.append(_strip_trailing_commas(f"{title} {nos_part} от {ds}"))
                first = False
            else:
                parts.append(_strip_trailing_commas(f"{nos_part} от {ds}"))

    return _strip_trailing_commas(", ".join(parts))


def build_docs_texts(act: Act) -> dict[str, str]:
    exec_schemes = list(act.attachments.filter(type=AttachmentType.EXEC_SCHEME).order_by("created_at", "id"))
    exec_scheme_text = _format_attachment(exec_schemes[0]) if exec_schemes else ""

    docs_registry = act.attachments.filter(type=AttachmentType.DOCS_REGISTRY).order_by("-created_at", "-id").first()

    exclude_types = {
        AttachmentType.EXEC_SCHEME,
        AttachmentType.MATERIALS_REGISTRY,
        AttachmentType.DOCS_REGISTRY,
        AttachmentType.APPROVALS_REGISTRY,
    }

    if docs_registry:
        # если есть реестр П-4, то в пункте 4 печатаем реестр
        other_docs_text = _format_attachment(docs_registry)
    else:
        other_docs = list(act.attachments.exclude(type__in=exclude_types).order_by("created_at", "id"))
        # ✅ FIX: группировка по title, как в "Приложениях"
        other_docs_text = _format_attachments_grouped_by_title(other_docs)

    return {"exec_scheme": exec_scheme_text, "other_docs": other_docs_text}


# -------------------------
# Approvals (Доп. сведения)
# -------------------------

def _lower_first(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    return s[:1].lower() + s[1:]


def build_approvals_text(act: Act) -> str:
    reg = act.attachments.filter(type=AttachmentType.APPROVALS_REGISTRY).order_by("-created_at", "-id").first()
    if reg:
        if getattr(reg, "doc_no", "") or getattr(reg, "doc_date", None) or getattr(reg, "doc_date_to", None):
            return _format_attachment(reg)
        return f"реестр №П-8.{act.number} от {fmt_date_g(act.act_date)}"

    items_qs = act.approval_items.select_related("approval").order_by("position", "id")
    items_count = items_qs.count()

    if items_count >= 5:
        return f"реестр №П-8.{act.number} от {fmt_date_g(act.act_date)}"

    descs = []
    for it in items_qs:
        txt = (it.label_override or "").strip()
        if not txt:
            txt = (getattr(it.approval, "description", "") or "").strip()
        txt = _lower_first(txt)
        txt = _strip_trailing_commas(txt)
        if txt:
            descs.append(txt)

    return _strip_trailing_commas(", ".join(descs))


# -------------------------
# Parties/signatories
# -------------------------

def _pick_nrs(person, act_date) -> tuple[str, str]:
    if not person or not act_date:
        return "", ""
    rec = (
        PersonNRS.objects.filter(person=person, is_active=True, valid_from__lte=act_date)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=act_date))
        .order_by("-valid_from", "-id")
        .first()
    )
    if not rec:
        return "", ""
    return _safe_str(rec.nrs_id), fmt_date_g(rec.valid_from)


def _resolve_authorization(act: Act, role: str, org_id: int | None, chosen: Authorization | None):
    if chosen:
        return chosen
    if not org_id or not act.act_date:
        return None
    return (
        Authorization.objects.select_related("person", "directive")
        .filter(organization_id=org_id, role=role, is_active=True, valid_from__lte=act.act_date)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=act.act_date))
        .order_by("-valid_from", "-id")
        .first()
    )


def _party(act: Act, role: str):
    return (
        act.parties.filter(role=role, is_enabled=True)
        .select_related("organization", "chosen_authorization")
        .order_by("position", "id")
        .first()
    )


def _parties(act: Act, role: str):
    return (
        act.parties.filter(role=role, is_enabled=True)
        .select_related("organization", "chosen_authorization")
        .order_by("position", "id")
    )


def _pick_sro_membership_only(org, kind: str, act_date):
    if not org:
        return {"name": "", "ogrn": "", "inn": ""}

    qs = org.sro_memberships.filter(kind=kind).filter(
        Q(date_from__isnull=True) | Q(date_from__lte=act_date),
        Q(date_to__isnull=True) | Q(date_to__gte=act_date),
    ).order_by("-date_from", "-id")

    sro = qs.first()
    if not sro:
        return {"name": "", "ogrn": "", "inn": ""}

    return {
        "name": _safe_str(getattr(sro, "sro_name", "")),
        "ogrn": _safe_str(getattr(sro, "sro_ogrn", "")),
        "inn": _safe_str(getattr(sro, "sro_inn", "")),
    }


def build_act_docx_context(act: Act) -> dict[str, str]:
    ctx: dict[str, str] = {}

    ctx["a_name"] = _safe_str(act.number)

    a = _date_parts_for_template(act.act_date)
    ctx["a_dd"], ctx["a_mm"], ctx["a_yy"] = a["dd"], a["mm"], a["yy"]

    s = _date_parts_for_template(act.work_start_date)
    ctx["s_dd"], ctx["s_mm"], ctx["s_yy"] = s["dd"], s["mm"], s["yy"]

    e = _date_parts_for_template(act.work_end_date)
    ctx["e_dd"], ctx["e_mm"], ctx["e_yy"] = e["dd"], e["mm"], e["yy"]

    ctx["job_name"] = _strip_trailing_commas(_safe_str(act.work_name))
    ctx["copies_count"] = _safe_str(act.copies_count)
    ctx["work_norms_text"] = _strip_trailing_commas(_safe_str(act.work_norms_text))
    ctx["allow_next_works_text"] = _strip_trailing_commas(_safe_str(act.allow_next_works_text))

    ctx.update(build_projects_text(act))

    ctx["passports"] = build_passports_text(act)
    docs = build_docs_texts(act)
    ctx["exec_scheme"] = docs["exec_scheme"]
    ctx["other_docs"] = docs["other_docs"]
    ctx["approvals"] = build_approvals_text(act)

    builder_rep_party = _party(act, ActRole.BUILDER_REP)
    ctx["builder_name"] = (
        _safe_str(getattr(builder_rep_party.organization, "full_name", ""))
        if (builder_rep_party and builder_rep_party.organization_id)
        else ""
    )

    def fill_role(prefix: str, role: str, *, include_nrs: bool, sro_build: bool = False, sro_design: bool = False):
        party = _party(act, role)
        org = party.organization if (party and party.organization_id) else None

        auth = _resolve_authorization(
            act=act,
            role=role,
            org_id=org.id if org else None,
            chosen=party.chosen_authorization if party else None,
        )

        person = auth.person if auth else None
        directive = auth.directive if auth else None

        ctx[f"{prefix}_org_full"] = _strip_trailing_commas(_safe_str(getattr(org, "full_name", "")))
        ctx[f"{prefix}_org_short"] = _strip_trailing_commas(_safe_str(getattr(org, "short_name", "")))

        ctx[f"{prefix}_org_ogrn"] = _safe_str(getattr(org, "ogrn", ""))
        ctx[f"{prefix}_org_inn"] = _safe_str(getattr(org, "inn", ""))
        ctx[f"{prefix}_org_address"] = _strip_trailing_commas(_safe_str(getattr(org, "address", "")))
        ctx[f"{prefix}_org_tel_fax"] = _strip_trailing_commas(_safe_str(getattr(org, "tel_fax", "")))

        ctx[f"{prefix}_position"] = _strip_trailing_commas(_safe_str(getattr(auth, "position_text", "")))
        ctx[f"{prefix}_fio"] = _normalize_fio_short(_safe_str(getattr(person, "short_name", "")))

        if include_nrs and person:
            nrs_num, nrs_date = _pick_nrs(person, act.act_date)
            ctx[f"{prefix}_nrs_number"] = nrs_num
            ctx[f"{prefix}_nrs_date"] = nrs_date
        else:
            ctx[f"{prefix}_nrs_number"] = ""
            ctx[f"{prefix}_nrs_date"] = ""

        ctx[f"{prefix}_directive_number"] = _safe_str(getattr(directive, "number", ""))
        ctx[f"{prefix}_directive_date"] = fmt_date_g(getattr(directive, "date", None))
        ctx[f"{prefix}_directive_note"] = _strip_trailing_commas(_safe_str(getattr(directive, "note", "")))

        if sro_build:
            sro = _pick_sro_membership_only(org, SroKind.BUILD, act.act_date)
            ctx[f"{prefix}_org_sro_builder"] = _strip_trailing_commas(sro["name"])
            ctx[f"{prefix}_org_sro_builder_ogrn"] = sro["ogrn"]
            ctx[f"{prefix}_org_sro_builder_inn"] = sro["inn"]

        if sro_design:
            sro = _pick_sro_membership_only(org, SroKind.DESIGN, act.act_date)
            ctx[f"{prefix}_org_sro_design"] = _strip_trailing_commas(sro["name"])
            ctx[f"{prefix}_org_sro_design_ogrn"] = sro["ogrn"]
            ctx[f"{prefix}_org_sro_design_inn"] = sro["inn"]

    tc_party = _party(act, ActRole.TECH_CUSTOMER_CONTROL)
    tc_org = tc_party.organization if (tc_party and tc_party.organization_id) else None

    ctx["tech_customer_org_full"] = _strip_trailing_commas(_safe_str(getattr(tc_org, "full_name", "")))
    ctx["tech_customer_org_short"] = _strip_trailing_commas(_safe_str(getattr(tc_org, "short_name", "")))
    ctx["tech_customer_org_ogrn"] = _safe_str(getattr(tc_org, "ogrn", ""))
    ctx["tech_customer_org_inn"] = _safe_str(getattr(tc_org, "inn", ""))
    ctx["tech_customer_org_address"] = _strip_trailing_commas(_safe_str(getattr(tc_org, "address", "")))
    ctx["tech_customer_org_tel_fax"] = _strip_trailing_commas(_safe_str(getattr(tc_org, "tel_fax", "")))

    tc_sro = _pick_sro_membership_only(tc_org, SroKind.BUILD, act.act_date)
    ctx["tech_customer_org_sro_builder"] = _strip_trailing_commas(tc_sro["name"])
    ctx["tech_customer_org_sro_ogrn"] = tc_sro["ogrn"]
    ctx["tech_customer_org_sro_inn"] = tc_sro["inn"]

    tc_auth = _resolve_authorization(
        act=act,
        role=ActRole.TECH_CUSTOMER_CONTROL,
        org_id=tc_org.id if tc_org else None,
        chosen=tc_party.chosen_authorization if tc_party else None,
    )
    tc_person = tc_auth.person if tc_auth else None
    tc_dir = tc_auth.directive if tc_auth else None

    ctx["tech_customer_position"] = _strip_trailing_commas(_safe_str(getattr(tc_auth, "position_text", "")))
    ctx["tech_customer_fio"] = _normalize_fio_short(_safe_str(getattr(tc_person, "short_name", "")))
    nrs_num, nrs_date = _pick_nrs(tc_person, act.act_date) if tc_person else ("", "")
    ctx["tech_customer_nrs_number"] = nrs_num
    ctx["tech_customer_nrs_date"] = nrs_date
    ctx["tech_customer_directive_number"] = _safe_str(getattr(tc_dir, "number", ""))
    ctx["tech_customer_directive_date"] = fmt_date_g(getattr(tc_dir, "date", None))
    ctx["tech_customer_directive_note"] = _strip_trailing_commas(_safe_str(getattr(tc_dir, "note", "")))

    fill_role("builder_rep", ActRole.BUILDER_REP, include_nrs=True, sro_build=True, sro_design=True)
    fill_role("builder_control", ActRole.BUILDER_CONTROL, include_nrs=True)
    fill_role("design_rep", ActRole.DESIGN_REP, include_nrs=False)
    fill_role("contractor_rep", ActRole.CONTRACTOR_REP, include_nrs=False)

    contractor_party = _party(act, ActRole.CONTRACTOR_REP)
    contractor_org = contractor_party.organization if (contractor_party and contractor_party.organization_id) else None
    ctx["contractor_rep_org_orgn"] = _safe_str(getattr(contractor_org, "ogrn", ""))

    others = list(_parties(act, ActRole.OTHER_REP))
    if others:
        fio_list: list[str] = []
        pos_list: list[str] = []
        org_full_list: list[str] = []
        org_short_list: list[str] = []
        note_list: list[str] = []

        first_dir_num = ""
        first_dir_date = ""

        for p in others:
            org = p.organization if p.organization_id else None
            auth = _resolve_authorization(act, ActRole.OTHER_REP, org.id if org else None, p.chosen_authorization)
            person = auth.person if auth else None
            directive = auth.directive if auth else None

            fio_list.append(_normalize_fio_short(_safe_str(getattr(person, "short_name", ""))))
            pos_list.append(_strip_trailing_commas(_safe_str(getattr(auth, "position_text", ""))))

            org_full_list.append(_strip_trailing_commas(_safe_str(getattr(org, "full_name", ""))))
            org_short_list.append(_strip_trailing_commas(_safe_str(getattr(org, "short_name", ""))))

            if not first_dir_num:
                first_dir_num = _safe_str(getattr(directive, "number", ""))
                first_dir_date = fmt_date_g(getattr(directive, "date", None))

            note_list.append(_strip_trailing_commas(_safe_str(getattr(directive, "note", ""))))

        ctx["other_rep_fio"] = _strip_trailing_commas(_join_non_empty(fio_list, sep=", "))
        ctx["other_rep_position"] = _strip_trailing_commas(_join_non_empty(pos_list, sep=", "))

        ctx["other_rep_org_full"] = _strip_trailing_commas(_join_non_empty(org_full_list, sep=", "))
        ctx["other_rep_org_short"] = _strip_trailing_commas(_join_non_empty(org_short_list, sep=", "))

        ctx["other_rep_directive_number"] = first_dir_num
        ctx["other_rep_directive_date"] = first_dir_date
        ctx["other_rep_directive_note"] = _strip_trailing_commas(_join_non_empty(note_list, sep=", "))
    else:
        ctx["other_rep_fio"] = ""
        ctx["other_rep_position"] = ""
        ctx["other_rep_org_full"] = ""
        ctx["other_rep_org_short"] = ""
        ctx["other_rep_directive_number"] = ""
        ctx["other_rep_directive_date"] = ""
        ctx["other_rep_directive_note"] = ""

    for k, v in list(ctx.items()):
        ctx[k] = _strip_trailing_commas("" if v is None else str(v))

    return ctx
