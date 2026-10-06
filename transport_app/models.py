from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone
from .formatting import format_place, format_site


class TransportUserSettings(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    values = models.JSONField(default=dict)
    skip_general = models.BooleanField("Пропускать общие данные", default=False)


class TransportRequest(models.Model):
    organization = models.CharField("Организация", max_length=200)
    site = models.CharField("Участок", max_length=100)
    place = models.CharField("Площадка", max_length=100)
    phone = models.CharField("Контактный телефон", max_length=40)
    date = models.DateField("Дата документа", default=timezone.localdate)
    author = models.CharField("Составил — должность, ФИО", max_length=200)
    reviewer = models.CharField("Согласующий — должность, ФИО", max_length=200)
    approver = models.CharField("Утверждающий — должность, ФИО", max_length=200)
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "Заявка на автотранспорт"
        verbose_name_plural = "Заявки на автотранспорт"

    def __str__(self):
        return f"Заявка №{self.pk} — {self.place}"

    @property
    def site_display(self):
        return format_site(self.site)

    @property
    def place_display(self):
        return format_place(self.place)

    @property
    def period(self):
        items = list(self.items.all())
        if not items:
            return "—"
        start, end = min(i.start for i in items), max(i.end for i in items)
        return start.strftime("%d.%m.%Y") if start == end else f"{start:%d.%m.%Y} — {end:%d.%m.%Y}"


class TransportItem(models.Model):
    request = models.ForeignKey(TransportRequest, related_name="items", on_delete=models.CASCADE)
    vehicle = models.CharField("Транспорт и характеристики", max_length=150)
    quantity = models.PositiveIntegerField("Количество единиц", default=1, validators=[MinValueValidator(1), MaxValueValidator(999)])
    mode = models.CharField("Срок", max_length=8, choices=[("day", "На один день"), ("period", "На период")], default="day")
    start = models.DateField("Дата / С")
    end = models.DateField("По, включительно")
    shift = models.CharField("Смена", max_length=2, choices=[("1", "1 смена"), ("2", "2 смена"), ("24", "Круглосуточно")], default="1")
    frequency = models.CharField("Периодичность работы", max_length=10, choices=[("daily", "Ежедневно"), ("alternate", "Через день, начиная с даты «С»")], default="daily")
    work = models.CharField("Виды работ", max_length=300)
    note = models.CharField("Примечание", max_length=300, blank=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name="transport_positive_quantity"),
            models.CheckConstraint(condition=models.Q(end__gte=models.F("start")), name="transport_valid_dates"),
        ]

    def clean(self):
        super().clean()
        if self.mode == "day" and self.start:
            self.end = self.start
            self.frequency = "daily"
        if self.start and self.end and self.end < self.start:
            raise ValidationError({"end": "Дата «По» не может быть раньше даты «С»."})
        if self.start and self.end and (self.end - self.start).days > 365:
            raise ValidationError({"end": "Период одной позиции не должен превышать один год."})

    @property
    def schedule(self):
        period = f"{self.start:%d.%m.%Y}" if self.start == self.end else f"С {self.start:%d.%m.%Y} по {self.end:%d.%m.%Y}"
        text = f"{period}\n{self.get_shift_display()}"
        if self.frequency == "alternate":
            text += "\nЧерез день"
        return text


class TransportPDF(models.Model):
    request = models.ForeignKey(TransportRequest, related_name="pdfs", on_delete=models.CASCADE)
    revision = models.PositiveIntegerField()
    content = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-revision"]
        constraints = [models.UniqueConstraint(fields=["request"], name="transport_single_pdf")]


class TransportEvent(models.Model):
    request = models.ForeignKey(TransportRequest, related_name="events", on_delete=models.CASCADE)
    text = models.CharField(max_length=250)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
