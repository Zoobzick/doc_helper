# acts_app/services/passport_suggestions.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.db import models
from django.db.models import Count, Max, OuterRef, Subquery, IntegerField, DateField, Value
from django.db.models.functions import Coalesce

from acts_app.models import ActMaterialItem


@dataclass(frozen=True)
class PassportSuggestionConfig:
    limit: int = 200
    prefer_recent_days: Optional[int] = None


class PassportSuggester:
    def __init__(self, project_id: int, config: PassportSuggestionConfig | None = None):
        self.project_id = project_id
        self.config = config or PassportSuggestionConfig()

    def queryset(self):
        from passports_app.models import Passport  # noqa: WPS433

        used_qs = (
            ActMaterialItem.objects
            .filter(
                act__projects__id=self.project_id,
                passport_id=OuterRef("pk"),
            )
            .values("passport_id")
        )

        used_count_sq = (
            used_qs.annotate(c=Count("id"))
            .values("c")[:1]
        )

        last_used_sq = (
            used_qs.annotate(d=Max("act__act_date"))
            .values("d")[:1]
        )

        qs = (
            Passport.objects
            .all()
            .annotate(
                used_count=Coalesce(Subquery(used_count_sq, output_field=IntegerField()), Value(0)),
                last_used=Subquery(last_used_sq, output_field=DateField()),
            )
            .annotate(
                was_used=models.Case(
                    models.When(used_count__gt=0, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            )
            .order_by(
                "-was_used",
                "-used_count",
                "-last_used",
                "-id",
            )
        )

        return qs[: self.config.limit]
