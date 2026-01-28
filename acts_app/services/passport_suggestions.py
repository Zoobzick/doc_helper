# acts_app/services/passport_suggestions.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.db.models import Count, Max, OuterRef, Subquery, IntegerField, DateField, Value
from django.db.models.functions import Coalesce

from acts_app.models import ActMaterialItem


@dataclass(frozen=True)
class PassportSuggestionConfig:
    """
    Настройки сортировки.
    """
    limit: int = 200               # сколько паспортов показываем в виджете/модалке
    prefer_recent_days: Optional[int] = None  # если захочешь "в приоритете за последние N дней" (пока не используем)


class PassportSuggester:
    """
    Возвращает queryset паспортов, отсортированный так, чтобы:
    1) сначала шли паспорта, которые уже использовали в актах по данному project,
    2) внутри — по частоте использования,
    3) затем — по дате последнего использования,
    4) затем — "как обычно" (например, по id/created_at паспорта — зависит от модели Passport).
    """

    def __init__(self, project_id: int, config: PassportSuggestionConfig | None = None):
        self.project_id = project_id
        self.config = config or PassportSuggestionConfig()

    def queryset(self):
        # Импорт локально, чтобы не завязаться жёстко на структуру passports_app при старте приложения
        from passports_app.models import Passport  # noqa: WPS433

        # Использования паспорта в актах данного проекта:
        # ActMaterialItem -> act -> project
        used_qs = (
            ActMaterialItem.objects
            .filter(
                act__project_id=self.project_id,
                passport_id=OuterRef("pk"),
            )
            .values("passport_id")
        )

        # сколько раз использовали
        used_count_sq = (
            used_qs.annotate(c=Count("id"))
            .values("c")[:1]
        )

        # когда последний раз использовали (по дате акта)
        last_used_sq = (
            used_qs.annotate(d=Max("act__act_date"))
            .values("d")[:1]
        )

        # Собираем основной queryset паспортов
        qs = (
            Passport.objects
            .all()
            .annotate(
                used_count=Coalesce(Subquery(used_count_sq, output_field=IntegerField()), Value(0)),
                last_used=Subquery(last_used_sq, output_field=DateField()),
            )
            .annotate(
                was_used=models.Case(  # type: ignore[attr-defined]
                    models.When(used_count__gt=0, then=Value(1)),  # type: ignore[attr-defined]
                    default=Value(0),
                    output_field=IntegerField(),
                )
            )
            .order_by(
                "-was_used",      # сначала использованные
                "-used_count",    # чаще — выше
                "-last_used",     # свежее использование — выше
                "-id",            # стабильный fallback (если у Passport нет created_at)
            )
        )

        return qs[: self.config.limit]


# Django ругается, если models не импортирован — поэтому локально:
from django.db import models  # noqa: E402
