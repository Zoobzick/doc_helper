from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count, IntegerField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate, TruncMonth
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from acts_app.models import Act
from projects_app.models import Project


CHART_COLORS = [
    "#4154f1",
    "#2eca6a",
    "#ff771d",
    "#e91e63",
    "#00a7c8",
    "#8e44ad",
    "#f1c40f",
    "#607d8b",
    "#d63384",
    "#198754",
]


def _chart_color(index: int) -> str:
    if index < len(CHART_COLORS):
        return CHART_COLORS[index]
    hue = (index * 137) % 360
    return f"hsl({hue}, 70%, 45%)"


def _parse_date(raw: str) -> date | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _month_range_filter_q(d_from: date, d_to: date) -> Q:
    y1, m1 = int(d_from.year), int(d_from.month)
    y2, m2 = int(d_to.year), int(d_to.month)

    if (y1, m1) > (y2, m2):
        y1, m1, y2, m2 = y2, m2, y1, m1

    left = Q(act_year__gt=y1) | (Q(act_year=y1) & Q(act_month__gte=m1))
    right = Q(act_year__lt=y2) | (Q(act_year=y2) & Q(act_month__lte=m2))
    return left & right


def _apply_month_period_filter(qs, d_from: date | None, d_to: date | None):
    if d_from and d_to:
        return qs.filter(_month_range_filter_q(d_from, d_to))
    if d_from:
        return qs.filter(
            Q(act_year__gt=d_from.year) |
            (Q(act_year=d_from.year) & Q(act_month__gte=d_from.month))
        )
    if d_to:
        return qs.filter(
            Q(act_year__lt=d_to.year) |
            (Q(act_year=d_to.year) & Q(act_month__lte=d_to.month))
        )
    return qs


def _apply_created_period_filter(qs, d_from: date | None, d_to: date | None):
    if d_from and d_to:
        return qs.filter(created_at__date__range=(d_from, d_to))
    if d_from:
        return qs.filter(created_at__date__gte=d_from)
    if d_to:
        return qs.filter(created_at__date__lte=d_to)
    return qs


def _user_label(row: dict) -> str:
    if not row.get("created_by_id"):
        return "Не указан"

    full_name = " ".join(
        part
        for part in (
            (row.get("created_by__last_name") or "").strip(),
            (row.get("created_by__first_name") or "").strip(),
        )
        if part
    )
    return full_name or row.get("created_by__email") or row.get("created_by__username") or "Пользователь"


def _period_label(value: date, period_group: str) -> str:
    if period_group == "month":
        return value.strftime("%m.%Y")
    return value.strftime("%d.%m.%Y")


def _period_bucket(value: date, period_group: str) -> date:
    if period_group == "month":
        return date(value.year, value.month, 1)
    return value


def _iter_period_values(start: date, end: date, period_group: str):
    current = _period_bucket(start, period_group)
    end = _period_bucket(end, period_group)
    while current <= end:
        yield current
        if period_group == "month":
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)
        else:
            current = current + timedelta(days=1)


class StatisticsDashboardView(LoginRequiredMixin, UserPassesTestMixin, View):
    template_name = "statistics_app/dashboard.html"

    def test_func(self):
        return self.request.user.is_staff

    def get(self, request: HttpRequest) -> HttpResponse:
        date_from = _parse_date(request.GET.get("date_from", ""))
        date_to = _parse_date(request.GET.get("date_to", ""))
        if date_from is not None and date_to is not None and date_from > date_to:
            date_from, date_to = date_to, date_from

        today = timezone.localdate()
        created_from = _parse_date(request.GET.get("created_from", "")) or today
        created_to = _parse_date(request.GET.get("created_to", "")) or today
        if created_from is not None and created_to is not None and created_from > created_to:
            created_from, created_to = created_to, created_from

        creator_id = (request.GET.get("created_by") or "").strip()
        project_id = (request.GET.get("project") or "").strip()
        q = (request.GET.get("q") or "").strip()
        period_group = (request.GET.get("period_group") or "day").strip()
        if period_group not in {"day", "month"}:
            period_group = "day"

        acts = _apply_month_period_filter(Act.objects.all(), date_from, date_to)
        if creator_id.isdigit():
            acts = acts.filter(created_by_id=int(creator_id))
        if project_id.isdigit():
            acts = acts.filter(projects__id=int(project_id))
        if q:
            acts = acts.filter(Q(number__icontains=q) | Q(work_name__icontains=q))

        acts = acts.distinct()

        totals = acts.aggregate(
            total=Count("id", distinct=True),
            sheets_total=Coalesce(Sum("sheets_total"), Value(0), output_field=IntegerField()),
        )

        creator_rows = list(
            acts.values(
                "created_by_id",
                "created_by__username",
                "created_by__email",
                "created_by__first_name",
                "created_by__last_name",
            )
            .annotate(
                count=Count("id", distinct=True),
                sheets_total=Coalesce(Sum("sheets_total"), Value(0), output_field=IntegerField()),
            )
            .order_by("-count", "created_by__last_name", "created_by__first_name")
        )

        creator_stats = []
        for index, row in enumerate(creator_rows):
            creator_key = row["created_by_id"] or "empty"
            color = _chart_color(index)
            creator_stats.append(
                {
                    "id": creator_key,
                    "label": _user_label(row),
                    "count": row["count"],
                    "sheets_total": row["sheets_total"],
                    "color": color,
                }
            )

        user_chart = {
            "labels": [item["label"] for item in creator_stats],
            "datasets": [
                {
                    "label": "Актов",
                    "data": [item["count"] for item in creator_stats],
                    "backgroundColor": [item["color"] for item in creator_stats],
                    "borderColor": [item["color"] for item in creator_stats],
                }
            ],
        }

        activity_acts = _apply_created_period_filter(acts, created_from, created_to)
        period_trunc = TruncMonth("created_at") if period_group == "month" else TruncDate("created_at")
        period_rows = list(
            activity_acts.annotate(period=period_trunc)
            .values(
                "period",
                "created_by_id",
                "created_by__username",
                "created_by__email",
                "created_by__first_name",
                "created_by__last_name",
            )
            .annotate(count=Count("id", distinct=True))
            .order_by("period", "created_by__last_name", "created_by__first_name")
        )

        period_values = []
        row_period_values = []
        creator_by_key = {}
        counts_by_creator_period = {}
        for row in period_rows:
            period = row["period"]
            if not period:
                continue
            if hasattr(period, "date"):
                period = period.date()
            period = _period_bucket(period, period_group)
            if period not in row_period_values:
                row_period_values.append(period)

            creator_key = row["created_by_id"] or "empty"
            if creator_key not in creator_by_key:
                creator_by_key[creator_key] = _user_label(row)
            counts_by_creator_period[(creator_key, period)] = row["count"]

        if created_from and created_to:
            period_values = list(_iter_period_values(created_from, created_to, period_group))
        else:
            period_values = row_period_values

        period_chart = {
            "labels": [_period_label(period, period_group) for period in period_values],
            "datasets": [
                {
                    "label": item["label"],
                    "data": [
                        counts_by_creator_period.get((item["id"], period), 0)
                        for period in period_values
                    ],
                    "backgroundColor": item["color"],
                    "borderColor": item["color"],
                }
                for item in creator_stats
                if item["id"] in creator_by_key
            ],
        }

        recent_acts = (
            acts.select_related("created_by")
            .prefetch_related("projects")
            .order_by("-act_date", "-id")[:15]
        )

        User = get_user_model()
        creators = (
            User.objects.filter(created_acts__isnull=False)
            .distinct()
            .order_by("last_name", "first_name", "email")
        )
        projects = (
            Project.objects.filter(acts__isnull=False)
            .distinct()
            .order_by("full_code", "id")
        )

        context = {
            "date_from": date_from,
            "date_to": date_to,
            "created_from": created_from,
            "created_to": created_to,
            "selected_creator_id": int(creator_id) if creator_id.isdigit() else None,
            "selected_project_id": int(project_id) if project_id.isdigit() else None,
            "period_group": period_group,
            "q": q,
            "creators": creators,
            "projects": projects,
            "totals": totals,
            "total_all_time": Act.objects.count(),
            "user_chart": user_chart,
            "period_chart": period_chart,
            "activity_total": activity_acts.count(),
            "creator_stats": creator_stats,
            "recent_acts": recent_acts,
        }
        return render(request, self.template_name, context)
