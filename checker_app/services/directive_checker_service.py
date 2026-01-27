import os
import re
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from docx import Document
from django.conf import settings
from django.db.models.functions import Coalesce

from directive_app.models import Directive


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




