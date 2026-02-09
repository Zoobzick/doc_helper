from datetime import date
from typing import Optional

def fmt_date_g(d: Optional[date]) -> str:
    return d.strftime("%d.%m.%Yг.") if d else ""

def fmt_date_range_g(d_from: Optional[date], d_to: Optional[date]) -> str:
    if not d_from:
        return ""
    if not d_to or d_to == d_from:
        return fmt_date_g(d_from)
    if d_from.year == d_to.year and d_from.month == d_to.month:
        return f"{d_from.day:02d}-{d_to.day:02d}.{d_from.month:02d}.{d_from.year}г."
    return f"{fmt_date_g(d_from)}-{fmt_date_g(d_to)}"
