import os
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import Http404, FileResponse, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import ListView

from .models import Material, Passport


class PassportsListView(PermissionRequiredMixin, ListView):
    """
    Список паспортов.
    """
    permission_required = "passports_app.view_passports_page"
    raise_exception = True

    model = Passport
    template_name = "passports_app/passports_list.html"
    context_object_name = "passports"

    def get_queryset(self):
        return Passport.objects.all().select_related("material")


@permission_required("passports_app.import_passports", raise_exception=True)
def import_passports_view(request):
    """
    Импорт паспортов - сканирование директории и обновление БД.
    """
    if request.method != "POST":
        # Чтобы импорт не запускали GET-ом случайно
        return redirect("passports_list")

    try:
        if not hasattr(settings, "PASSPORTS_DIR"):
            messages.error(request, "Не указан путь к паспортам в настройках")
            return redirect("passports_list")

        passports_dir = settings.PASSPORTS_DIR

        if not os.path.exists(passports_dir):
            messages.error(request, f"Директория не существует: {passports_dir}")
            return redirect("passports_list")

        all_files = []
        for root, dirs, files in os.walk(passports_dir):
            for file in files:
                if not file.startswith("."):
                    file_path = os.path.join(root, file)
                    all_files.append((file_path, file))

        if not all_files:
            messages.warning(request, f"В папке {passports_dir} не найдено файлов")
            return redirect("passports_list")

        stats = {
            "total": len(all_files),
            "saved": 0,
            "updated": 0,
            "errors": [],
            "materials_created": 0,
        }

        material_cache = {}

        for file_path, filename in all_files:
            try:
                parsed_data = parse_filename(filename)
                if parsed_data is None:
                    stats["errors"].append(f"{filename}: Не удалось распарсить")
                    continue

                material_name = parsed_data.get("material_name") or "Неизвестный материал"
                material = get_or_create_material(material_name, material_cache, stats)

                document_name = parsed_data.get("document_name", filename)
                passport_number = parsed_data.get("passport_number") or ""
                document_date = parsed_data.get("document_date")  # может быть None

                existing = Passport.objects.filter(file_name=filename).first()

                if existing:
                    existing.material = material
                    existing.document_name = document_name
                    existing.document_number = passport_number
                    if document_date:
                        existing.document_date = document_date
                    existing.file_path = file_path
                    existing.save()
                    stats["updated"] += 1
                else:
                    Passport.objects.create(
                        material=material,
                        document_name=document_name,
                        document_number=passport_number,
                        document_date=document_date,
                        consumption=0,
                        file_name=filename,
                        file_path=file_path,
                    )
                    stats["saved"] += 1

            except Exception as e:
                stats["errors"].append(f"{filename}: {str(e)}")

        message_parts = [
            "<strong>✅ Импорт завершен!</strong>",
            "<strong>📊 Статистика:</strong>",
            f"• Всего файлов: {stats['total']}",
            f"• Добавлено новых: {stats['saved']}",
            f"• Обновлено: {stats['updated']}",
        ]

        if stats["materials_created"] > 0:
            message_parts.append(f"• Создано материалов: {stats['materials_created']}")

        if stats["errors"]:
            message_parts.append(f"• Ошибок: {len(stats['errors'])}")

            message_parts.append("<br><strong>⚠️ Примеры ошибок:</strong>")
            for i, error in enumerate(stats["errors"][:3], 1):
                message_parts.append(f"{i}. {error}")

        messages.success(request, "<br>".join(message_parts))

    except Exception as e:
        messages.error(request, f"❌ Ошибка при импорте: {str(e)}")

    return redirect("passports_list")


def parse_filename(filename):
    """
    Парсер имени файла.
    """
    if not filename:
        return None

    name_without_ext = os.path.splitext(filename)[0]

    if filename.lower().endswith(".pdf"):
        try:
            if "(" in name_without_ext and ")" in name_without_ext and "№" in name_without_ext and " от " in name_without_ext:
                mat_name = name_without_ext.rsplit("(")[0].strip()
                in_brackets = name_without_ext.rsplit("(")[1].split(")")[0]

                doc_name = in_brackets.split("№")[0].strip()
                rest = in_brackets.split("№")[1]

                passport_number = rest.split(" от ")[0].strip()
                date_str = rest.split(" от ")[1].strip()

                document_date = None
                for date_format in ["%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"]:
                    try:
                        document_date = datetime.strptime(date_str, date_format).date()
                        break
                    except Exception:
                        continue

                return {
                    "material_name": mat_name,
                    "document_name": doc_name,
                    "passport_number": passport_number,
                    "document_date": document_date,
                    "filename": filename,
                }
        except Exception:
            pass

    return {
        "material_name": None,
        "document_name": name_without_ext,
        "passport_number": None,
        "document_date": None,
        "filename": filename,
    }


def get_or_create_material(material_name, cache, stats):
    """
    Получает или создаёт Material.
    """
    material_name_clean = material_name.strip().title()

    if material_name_clean not in cache:
        try:
            material_obj = Material.objects.get(name=material_name_clean)
        except Material.DoesNotExist:
            material_obj = Material.objects.create(name=material_name_clean)
            stats["materials_created"] += 1

        cache[material_name_clean] = material_obj

    return cache[material_name_clean]


@permission_required("passports_app.open_passport_file", raise_exception=True)
def view_pdf(request, pk):
    """
    Открывает PDF файл в браузере.
    """
    passport = get_object_or_404(Passport, pk=pk)
    file_path = f"{passport.file_path}"

    if os.path.exists(file_path) and passport.file_name.lower().endswith(".pdf"):
        try:
            response = FileResponse(open(file_path, "rb"), content_type="application/pdf")
            response["Content-Disposition"] = f'inline; filename="{passport.file_name}"'
            return response
        except Exception as e:
            return HttpResponse(f"Ошибка открытия файла: {str(e)}", status=500)

    return HttpResponse("Файл не найден или не является PDF", status=404)


@login_required
@permission_required("passports_app.change_passport_consumption", raise_exception=True)
def update_consumption(request, pk):
    """
    Обновление расхода с возвратом на страницу списка паспортов.
    """
    passport = get_object_or_404(Passport, pk=pk)

    if request.method == "POST":
        consumption_value = request.POST.get("consumption")

        if consumption_value:
            try:
                value = float(consumption_value)
                if 0 <= value <= 100:
                    passport.consumption = value
                    passport.save()
                    messages.success(request, f"✅ Расход обновлен: {value}")
                else:
                    messages.error(request, "❌ Значение должно быть от 0 до 100")
            except ValueError:
                messages.error(request, "❌ Введите корректное число")
        else:
            messages.error(request, "❌ Введите значение расхода")

    return HttpResponseRedirect(reverse("passports_list"))
