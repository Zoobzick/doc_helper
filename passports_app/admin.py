# admin.py
from django.contrib import admin
from django.urls import path
from django.shortcuts import redirect
from django.conf import settings
from .models import Passport
# from .services.passport_scanner import PassportScanner
#
#
# @admin.register(Passport)
# class PassportAdmin(admin.ModelAdmin):
#     list_display = ['document_name', 'document_number', 'material', 'file_name', 'created']
#     list_filter = ['material', 'document_date']
#
#     def get_urls(self):
#         urls = super().get_urls()
#         custom_urls = [
#             path('sync/', self.admin_site.admin_view(self.sync_view), name='passport_sync'),
#         ]
#         return custom_urls + urls
#
#     def sync_view(self, request):
#         """Простой обработчик синхронизации"""
#         base_path = getattr(settings, 'PASSPORTS_DIR', '/папка/с/паспортами')
#         scanner = PassportScanner(base_path)
#         result = scanner.sync_with_database()
#
#         self.message_user(
#             request,
#             f"Добавлено {result['added']} новых паспортов из {result['total_files']} файлов"
#         )
#         return redirect('..')