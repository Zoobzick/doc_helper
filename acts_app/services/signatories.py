# acts_app/services/signatories.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from directive_app.models import Authorization, ActRole
from acts_app.models import Act, ActParty, ActSignatorySnapshot


# --- DTO/результаты резолва -------------------------------------------------

class PartyResolveStatus:
    DISABLED = "DISABLED"            # (is_enabled)=False
    MISSING_ORG = "MISSING_ORG"      # (organization)=None
    NO_AUTH = "NO_AUTH"              # 0 кандидатов Authorization на дату
    ONE_AUTH = "ONE_AUTH"            # 1 кандидат (автовыбор)
    MANY_AUTH_NEED_CHOICE = "MANY_AUTH_NEED_CHOICE"  # >1 кандидат, нужен ручной выбор
    CHOSEN_AUTH = "CHOSEN_AUTH"      # выбран (chosen_authorization)


@dataclass(frozen=True)
class ResolvedParty:
    """
    Результат расчёта одной строки таблицы "Подписанты".
    """
    party: ActParty                          # (party) строка role-organization в акте
    status: str                              # (status) PartyResolveStatus.*
    candidates: list[Authorization]          # (candidates) список возможных Authorization на дату (для модалки выбора)
    effective_authorization: Optional[Authorization]  # (effective_authorization) что реально покажем в колонках Приказ/Персона


# --- Низкоуровневые селекторы ----------------------------------------------

def _authorization_candidates_qs(
    *,
    organization_id: int,
    role: str,
    act_date,
):
    """
    Возвращает QuerySet кандидатов Authorization, действующих на (act_date).
    """
    return (
        Authorization.objects
        .select_related("person", "directive", "directive__issuer_organization")
        .filter(
            is_active=True,
            organization_id=organization_id,
            role=role,
            valid_from__lte=act_date,
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=act_date))
        # Стабильная сортировка: свежие полномочия выше
        .order_by("-valid_from", "-created_at", "-id")
    )


def get_candidates_for_party(party: ActParty, act_date) -> list[Authorization]:
    """
    Получить кандидатов Authorization для строки (party) на дату (act_date).

    Возвращает список по приоритету (свежие выше).
    """
    if not party.organization_id:
        return []

    return list(
        _authorization_candidates_qs(
            organization_id=party.organization_id,
            role=party.role,
            act_date=act_date,
        )
    )


def build_directive_repr(auth: Authorization) -> str:
    """
    Сборка печатного представления основания.
    Формат можно менять централизованно здесь.
    """
    # (directive) документ-основание
    directive = auth.directive
    # (issuer) организация-выдавшая документ
    issuer = directive.issuer_organization.short_name

    # Пример: "Приказ №12 от 01.02.2026 (ООО Ромашка)"
    return f"{directive.get_doc_type_display()} №{directive.number} от {directive.date:%d.%m.%Y} ({issuer})"


# --- Резолв строки таблицы --------------------------------------------------

def resolve_party(party: ActParty, act_date) -> ResolvedParty:
    """
    Рассчитать статус строки таблицы "Подписанты" и то, что отображать в колонках Приказ/Персона.
    """
    # 1) выключена — не участвует
    if not party.is_enabled:
        return ResolvedParty(
            party=party,
            status=PartyResolveStatus.DISABLED,
            candidates=[],
            effective_authorization=None,
        )

    # 2) организация не выбрана
    if not party.organization_id:
        return ResolvedParty(
            party=party,
            status=PartyResolveStatus.MISSING_ORG,
            candidates=[],
            effective_authorization=None,
        )

    # 3) если пользователь уже выбрал вручную — показываем выбранное,
    #    но дополнительно проверяем, что оно всё ещё валидно на дату
    if party.chosen_authorization_id:
        chosen = (
            Authorization.objects
            .select_related("person", "directive", "directive__issuer_organization")
            .filter(id=party.chosen_authorization_id)
            .first()
        )
        if chosen and _is_authorization_valid_for(party, chosen, act_date):
            return ResolvedParty(
                party=party,
                status=PartyResolveStatus.CHOSEN_AUTH,
                candidates=[],
                effective_authorization=chosen,
            )
        # выбранное больше не подходит (сменили дату/организацию) → ведём себя как без выбора
        candidates = get_candidates_for_party(party, act_date)
        return _resolve_by_candidates(party, candidates)

    # 4) иначе — резолв по кандидатам
    candidates = get_candidates_for_party(party, act_date)
    return _resolve_by_candidates(party, candidates)


def _resolve_by_candidates(party: ActParty, candidates: list[Authorization]) -> ResolvedParty:
    # 0 кандидатов
    if len(candidates) == 0:
        return ResolvedParty(
            party=party,
            status=PartyResolveStatus.NO_AUTH,
            candidates=[],
            effective_authorization=None,
        )
    # 1 кандидат — автоподстановка
    if len(candidates) == 1:
        return ResolvedParty(
            party=party,
            status=PartyResolveStatus.ONE_AUTH,
            candidates=[],
            effective_authorization=candidates[0],
        )
    # >1 — конфликт, обязательный ручной выбор
    return ResolvedParty(
        party=party,
        status=PartyResolveStatus.MANY_AUTH_NEED_CHOICE,
        candidates=candidates,
        effective_authorization=None,
    )


def _is_authorization_valid_for(party: ActParty, auth: Authorization, act_date) -> bool:
    """
    Проверка, что конкретная Authorization подходит для строки (party) на дату.
    """
    if not party.organization_id:
        return False
    if auth.organization_id != party.organization_id:
        return False
    if auth.role != party.role:
        return False
    if not auth.is_active:
        return False
    if auth.valid_from > act_date:
        return False
    if auth.valid_to and auth.valid_to < act_date:
        return False
    return True


# --- Резолв всего акта ------------------------------------------------------

def resolve_act_parties(act: Act) -> list[ResolvedParty]:
    """
    Рассчитать все строки "Подписанты" для акта.
    """
    # (act_date) дата акта
    act_date = act.act_date
    parties = list(act.parties.select_related("organization").all())
    return [resolve_party(party, act_date) for party in parties]


# --- Изменения, которые должны сбрасывать chosen_authorization --------------

@transaction.atomic
def reset_choices_for_act_on_date_change(act: Act) -> int:
    """
    При смене даты акта: сбрасываем chosen_authorization у всех включённых строк.
    Возвращает (count) сколько строк сбросили.
    """
    return (
        ActParty.objects
        .filter(act=act, is_enabled=True)
        .exclude(chosen_authorization__isnull=True)
        .update(chosen_authorization=None)
    )


@transaction.atomic
def reset_choice_for_party_on_org_change(party: ActParty) -> None:
    """
    При смене организации в строке: сбрасываем chosen_authorization у этой строки.
    """
    if party.chosen_authorization_id:
        party.chosen_authorization = None
        party.save(update_fields=["chosen_authorization"])


# --- Ручной выбор (обязательный при конфликте) ------------------------------

@transaction.atomic
def choose_authorization_for_party(*, party: ActParty, authorization_id: int) -> None:
    """
    Пользователь вручную выбирает конкретное полномочие (Authorization) для строки (party).
    Проверяем, что оно валидно на дату акта.
    """
    # (act_date) дата акта
    act_date = party.act.act_date

    auth = (
        Authorization.objects
        .select_related("person", "directive", "directive__issuer_organization")
        .filter(id=authorization_id)
        .first()
    )
    if not auth:
        raise ValidationError("Выбранное полномочие не найдено.")

    if not _is_authorization_valid_for(party, auth, act_date):
        raise ValidationError("Выбранное полномочие не подходит для этой роли/организации/даты акта.")

    party.chosen_authorization = auth
    party.save(update_fields=["chosen_authorization"])


# --- Валидация перед финализацией ------------------------------------------

def validate_before_finalize(act: Act) -> None:
    """
    Строгая валидация перед переводом акта в FINAL.
    Валидируем только строки, где is_enabled=True.

    Бросает ValidationError с удобным сообщением (можно показывать пользователю).
    """
    errors: dict[str, list[str]] = {}

    # (act_date) дата акта
    act_date = act.act_date

    parties: list[ActParty] = list(
        act.parties.select_related("organization").all()
    )

    for party in parties:
        if not party.is_enabled:
            continue

        key = f"party:{party.id}"

        # 1) организация обязательна
        if not party.organization_id:
            errors.setdefault(key, []).append(f"{party.get_role_display()}: не выбрана организация.")
            continue

        candidates = get_candidates_for_party(party, act_date)

        # 2) должен быть хотя бы 1 кандидат
        if len(candidates) == 0:
            errors.setdefault(key, []).append(
                f"{party.get_role_display()}: нет полномочий на дату {act_date:%d.%m.%Y}."
            )
            continue

        # 3) конфликт -> обязателен chosen_authorization
        if len(candidates) > 1:
            if not party.chosen_authorization_id:
                errors.setdefault(key, []).append(
                    f"{party.get_role_display()}: найдено несколько полномочий — нужен ручной выбор."
                )
                continue

            # и выбранное должно быть валидно
            chosen = party.chosen_authorization
            if not chosen or not _is_authorization_valid_for(party, chosen, act_date):
                errors.setdefault(key, []).append(
                    f"{party.get_role_display()}: выбранное полномочие больше не подходит (проверь дату/организацию)."
                )
                continue

        # 4) если кандидат один — chosen_authorization не требуется (и может быть пустым)
        #    (ничего не делаем)

    if errors:
        # Можно отдать эту структуру в UI, но базово — единое сообщение
        flat = []
        for msgs in errors.values():
            flat.extend(msgs)
        raise ValidationError(flat)


# --- Фиксация снапшотов при FINAL ------------------------------------------

@transaction.atomic
def freeze_signatories_to_snapshots(act: Act) -> None:
    """
    Создаёт снапшоты подписантов для акта.
    Предполагается, что validate_before_finalize(act) уже прошла.

    Стратегия:
    - удаляем старые снапшоты
    - создаём новые из effective Authorization для каждой включённой строки
    """
    validate_before_finalize(act)

    act_date = act.act_date

    # (parties) строки подписантов в порядке печати
    parties: list[ActParty] = list(
        act.parties.select_related("organization", "chosen_authorization").order_by("position", "id")
    )

    ActSignatorySnapshot.objects.filter(act=act).delete()

    snapshots: list[ActSignatorySnapshot] = []

    for party in parties:
        if not party.is_enabled:
            continue

        candidates = get_candidates_for_party(party, act_date)

        if len(candidates) == 1:
            effective = candidates[0]
        else:
            # конфликт уже гарантированно закрыт выбором
            effective = party.chosen_authorization

        # (organization_name) как печатаем организацию
        organization_name = party.organization.full_name if party.organization else ""

        snapshots.append(
            ActSignatorySnapshot(
                act=act,
                role=party.role,
                position=party.position,
                source_authorization_uuid=getattr(effective, "uuid", None),
                source_directive_uuid=getattr(effective.directive, "uuid", None),
                organization_name=organization_name,
                person_fio=effective.person.full_name,
                position_text=effective.position_text or "",
                directive_repr=build_directive_repr(effective),
            )
        )

    ActSignatorySnapshot.objects.bulk_create(snapshots)
