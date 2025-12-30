from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class Material(models.Model):
    name = models.CharField(
        max_length=255,
        verbose_name="Наименование материала",
        unique=True
    )

    class Meta:
        db_table = "materials"
        verbose_name = "Материал"
        verbose_name_plural = "Материалы"
        unique_together = ['name']

    def __str__(self):
        return self.name


class Passport(models.Model):
    material = models.ForeignKey(
        Material,
        on_delete=models.PROTECT,
        verbose_name="Материал",
        related_name="passports",
        help_text="Выберите материал из списка"
    )

    document_name = models.CharField(max_length=255, verbose_name="Наименование документа")
    document_number = models.CharField(max_length=100, verbose_name="Номер документа", blank=True)
    document_date = models.DateField(verbose_name="Дата документа", blank=True, null=True)

    consumption = models.IntegerField(
        verbose_name="Расход",
        validators=[
            MinValueValidator(0, message="Расход не может быть меньше 0"),
            MaxValueValidator(100, message="Расход не может быть больше 100")
        ],
        default=0,
        help_text="Значение от 0 до 100%"
    )

    file_name = models.CharField(
        max_length=255,
        verbose_name="Имя файла",
        unique=True,
        help_text="Имя файла на диске"
    )

    file_path = models.CharField(
        max_length=1000,
        verbose_name="Путь к файлу",
        help_text="Полный путь к файлу на диске"
    )

    created = models.DateField(auto_now_add=True, verbose_name="Добавлено", editable=False)

    class Meta:
        db_table = "passports"
        verbose_name = "Паспорт"
        verbose_name_plural = "Паспорта"

        # ✅ Права "по страницам/действиям" (как в approvals/projects)
        permissions = [
            ("view_passports_page", "Может открывать страницу списка паспортов"),
            ("import_passports", "Может импортировать паспорта (сканирование папки)"),
            ("open_passport_file", "Может открывать файл паспорта (PDF)"),
            ("change_passport_consumption", "Может менять расход паспорта"),
        ]

    def __str__(self):
        number = f" №{self.document_number}" if self.document_number else ""
        return f"{self.material.name.capitalize()} {self.document_name}{number}"
