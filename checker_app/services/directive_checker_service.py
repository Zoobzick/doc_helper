import os
import re
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from docx import Document
from django.conf import settings
from django.db.models.functions import Coalesce

from directive_app.models import Directive, SignerRoleConfig


# -------------------- парсинг ФИО --------------------
_FIO_RE = re.compile(r"[А-ЯЁ][а-яё\-]+ [А-ЯЁ]\.[А-ЯЁ]\.", re.UNICODE)


# -------------------- результаты --------------------
@dataclass
class RoleDirectiveCheck:
    role: str
    role_label: str
    paragraph: int
    fio: Optional[str]
    act_order_number: Optional[str]
    act_order_date: Optional[date]
    db_order_number: Optional[str]
    db_order_date: Optional[date]
    status: str
    details: str


@dataclass
class ActDirectiveCheckResult:
    file_path: str
    act_date: Optional[date]
    checks: list[RoleDirectiveCheck]


# -------------------- утилиты --------------------
def _parse_date_ddmmyyyy(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y").date()
    except Exception:
        return None


def _replace_month(month: str) -> str:
    dates = {
        "января": "01", "февраля": "02", "марта": "03", "апреля": "04",
        "мая": "05", "июня": "06", "июля": "07", "августа": "08",
        "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12",
    }
    return dates.get((month or "").strip().lower(), month)


def _clean_year_piece(s: str) -> str:
    # (s) — кусок года из ячейки таблицы (может содержать "г."/"г")
    return (s or "").replace("г.", "").replace("г", "").strip()


def _get_act_date_from_doc(doc: Document) -> Optional[date]:
    """
    Дата акта из таблицы #1:
    tables[1].rows[0].cells[3] = день
    tables[1].rows[0].cells[5] = месяц словом
    tables[1].rows[0].cells[6..8] = год частями (может содержать "г.")
    """
    try:
        row = doc.tables[1].rows[0]

        day = (row.cells[3].text or "").strip()
        month_word = (row.cells[5].text or "").strip()
        month = _replace_month(month_word)

        year = (
            _clean_year_piece(row.cells[6].text)
            + _clean_year_piece(row.cells[7].text)
            + _clean_year_piece(row.cells[8].text)
        ).strip()
        year = year.replace("г.", "").replace("г", "").strip()

        return datetime.strptime(f"{day}.{month}.{year}", "%d.%m.%Y").date()
    except Exception:
        return None


def _normalize_order_number(num: str) -> str:
    return (
        (num or "")
        .replace("№", "")
        .replace("\u00a0", "")
        .replace(" ", "")
        .strip()
        .upper()
    )


def _extract_last_fio(text_left: str) -> Optional[str]:
    matches = _FIO_RE.findall(text_left or "")
    return matches[-1].strip() if matches else None


def _extract_order_and_fio_from_paragraph(text: str) -> tuple[Optional[str], Optional[str], Optional[date]]:
    """
    (fio, order_number, order_date)

    Логика как у тебя:
      - split("№", 1)
      - rsplit(" от ", 1) => берём последнее "от"
      - дату чистим от "г/г."
    """
    if not text:
        return None, None, None

    if "№" not in text:
        fio = _extract_last_fio(text)
        return fio, None, None

    left, right = text.split("№", 1)
    fio = _extract_last_fio(left)

    right = (right or "").strip()
    if " от " not in right:
        order_number = right.strip().rstrip(".,;:")
        return fio, (order_number or None), None

    order_number_part, date_part = right.rsplit(" от ", 1)

    order_number = (order_number_part or "").strip().rstrip(".,;:")
    date_raw = (
        (date_part or "")
        .replace("г.", "")
        .replace("г", "")
        .strip()
        .rstrip(".,;:")
    )
    order_date = _parse_date_ddmmyyyy(date_raw)

    return fio, (order_number or None), order_date


# -------------------- пути --------------------
def _month_folder_name(month: int) -> str:
    month_map = {
        1: "01. Январь", 2: "02. Февраль", 3: "03. Март", 4: "04. Апрель",
        5: "05. Май", 6: "06. Июнь", 7: "07. Июль", 8: "08. Август",
        9: "09. Сентябрь", 10: "10. Октябрь", 11: "11. Ноябрь", 12: "12. Декабрь",
    }
    return month_map[month]


def build_month_path(year: int, month: int) -> Path:
    base = Path(settings.BASE_ID_DIR)
    return base / str(year) / _month_folder_name(month)


def find_acts_folder(month_path: Path) -> Optional[Path]:
    if not month_path.exists():
        return None

    for p in month_path.iterdir():
        if p.is_dir() and p.name.startswith("Акты"):
            return p
    return None


def find_act_docx_files(acts_folder: Path) -> list[str]:
    """
    Только .docx, которые начинаются с "Акт"
    """
    result: list[str] = []
    for root, _, files in os.walk(acts_folder):
        for fn in files:
            if fn.lower().endswith(".docx") and fn.startswith("Акт"):
                result.append(os.path.join(root, fn))
    return sorted(result)


# -------------------- конфиги ролей --------------------
def get_role_configs(selected_roles: Optional[list[str]] = None) -> list[SignerRoleConfig]:
    """
    Берём включённые роли с заданным параграфом.
    Если selected_roles передан — фильтруем по нему.
    """
    qs = (
        SignerRoleConfig.objects
        .filter(is_enabled=True)
        .exclude(source_paragraph__isnull=True)
        .only("role", "source_paragraph", "is_enabled")
        .order_by("role")
    )
    if selected_roles:
        qs = qs.filter(role__in=selected_roles)
    return list(qs)


# -------------------- БД: выбрать "самый свежий приказ" --------------------
def _expected_directive_for_fio_role(fio: str, role: str, act_date: date) -> Optional[Directive]:
    """
    Самый свежий приказ на дату акта для (fio + role).

    resolved_date = COALESCE(effective_date, date)
    resolved_date <= act_date
    """
    return (
        Directive.objects
        .filter(employee_full_name=fio, signer_role=role, is_active=True)
        .annotate(effective_date_resolved_db=Coalesce("effective_date", "date"))
        .filter(effective_date_resolved_db__lte=act_date)
        .order_by("-effective_date_resolved_db", "-date")
        .first()
    )


# -------------------- проверка 1 акта --------------------
def check_single_act(file_path: str, role_configs: list[SignerRoleConfig]) -> ActDirectiveCheckResult:
    doc = Document(file_path)
    act_date = _get_act_date_from_doc(doc)

    checks: list[RoleDirectiveCheck] = []

    if not act_date:
        for cfg in role_configs:
            checks.append(RoleDirectiveCheck(
                role=cfg.role,
                role_label=cfg.get_role_display(),
                paragraph=int(cfg.source_paragraph),
                fio=None,
                act_order_number=None,
                act_order_date=None,
                db_order_number=None,
                db_order_date=None,
                status="PARSE_ERROR",
                details="Не удалось определить дату акта (таблица #1).",
            ))
        return ActDirectiveCheckResult(file_path=file_path, act_date=None, checks=checks)

    for cfg in role_configs:
        role = cfg.role
        role_label = cfg.get_role_display()
        paragraph = int(cfg.source_paragraph)

        if paragraph < 0 or paragraph >= len(doc.paragraphs):
            checks.append(RoleDirectiveCheck(
                role=role,
                role_label=role_label,
                paragraph=paragraph,
                fio=None,
                act_order_number=None,
                act_order_date=None,
                db_order_number=None,
                db_order_date=None,
                status="PARSE_ERROR",
                details=f"Параграф #{paragraph} вне диапазона документа.",
            ))
            continue

        p_text = doc.paragraphs[paragraph].text or ""
        fio, act_num, act_dt = _extract_order_and_fio_from_paragraph(p_text)

        if not fio:
            checks.append(RoleDirectiveCheck(
                role=role,
                role_label=role_label,
                paragraph=paragraph,
                fio=None,
                act_order_number=act_num,
                act_order_date=act_dt,
                db_order_number=None,
                db_order_date=None,
                status="NO_FIO",
                details=f"Параграф #{paragraph}: не найдено ФИО.",
            ))
            continue

        if not act_num or not act_dt:
            checks.append(RoleDirectiveCheck(
                role=role,
                role_label=role_label,
                paragraph=paragraph,
                fio=fio,
                act_order_number=act_num,
                act_order_date=act_dt,
                db_order_number=None,
                db_order_date=None,
                status="NO_ORDER_IN_ACT",
                details=f"{fio}: не удалось извлечь номер/дату приказа в параграфе #{paragraph}.",
            ))
            continue

        if act_date < act_dt:
            checks.append(RoleDirectiveCheck(
                role=role,
                role_label=role_label,
                paragraph=paragraph,
                fio=fio,
                act_order_number=act_num,
                act_order_date=act_dt,
                db_order_number=None,
                db_order_date=None,
                status="ACT_DATE_LT_ORDER",
                details=f"{fio}: дата акта {act_date} < даты приказа в акте {act_dt}.",
            ))
            continue

        expected = _expected_directive_for_fio_role(fio, role, act_date)
        if not expected:
            checks.append(RoleDirectiveCheck(
                role=role,
                role_label=role_label,
                paragraph=paragraph,
                fio=fio,
                act_order_number=act_num,
                act_order_date=act_dt,
                db_order_number=None,
                db_order_date=None,
                status="NO_IN_DB",
                details=f"{fio}: в БД нет приказов для роли {role_label} (проверка пропущена).",
            ))
            continue

        db_num = expected.number
        db_dt = getattr(expected, "effective_date_resolved_db", None)

        # если в акте приказ старее, чем должен быть по БД — на переделку
        if db_dt and act_dt < db_dt:
            checks.append(RoleDirectiveCheck(
                role=role,
                role_label=role_label,
                paragraph=paragraph,
                fio=fio,
                act_order_number=act_num,
                act_order_date=act_dt,
                db_order_number=db_num,
                db_order_date=db_dt,
                status="OUTDATED",
                details=(
                    f"Подписант: {fio}\n"
                    f"Дата акта: {act_date}\n"
                    f"В акте: №{act_num} от {act_dt}\n"
                    f"Должен быть: №{db_num} от {db_dt}"
                )
                ,
            ))
            continue

        # если даты не хуже — сверим номер (после нормализации)
        if _normalize_order_number(act_num) != _normalize_order_number(db_num):
            checks.append(RoleDirectiveCheck(
                role=role,
                role_label=role_label,
                paragraph=paragraph,
                fio=fio,
                act_order_number=act_num,
                act_order_date=act_dt,
                db_order_number=db_num,
                db_order_date=db_dt,
                status="MISMATCH",
                details=(
                    f"Подписант: {fio}\n"
                    f"Дата акта: {act_date}\n"
                    f"В акте: №{act_num} от {act_dt}\n"
                    f"Должен быть: №{db_num} от {db_dt}"
                ),
            ))
            continue

        checks.append(RoleDirectiveCheck(
            role=role,
            role_label=role_label,
            paragraph=paragraph,
            fio=fio,
            act_order_number=act_num,
            act_order_date=act_dt,
            db_order_number=db_num,
            db_order_date=db_dt,
            status="OK",
            details=f"{fio}: приказ актуален на дату акта {act_date}.",
        ))

    return ActDirectiveCheckResult(file_path=file_path, act_date=act_date, checks=checks)


# -------------------- проверка месяца --------------------
def check_directives_for_month(
    year: int,
    month: int,
    selected_roles: Optional[list[str]] = None,
) -> tuple[Path, Optional[Path], list[ActDirectiveCheckResult]]:
    month_path = build_month_path(year, month)
    acts_folder = find_acts_folder(month_path)
    if not acts_folder:
        return month_path, None, []

    role_configs = get_role_configs(selected_roles=selected_roles)
    act_files = find_act_docx_files(acts_folder)

    results: list[ActDirectiveCheckResult] = []
    for file_path in act_files:
        results.append(check_single_act(file_path, role_configs))

    return month_path, acts_folder, results

