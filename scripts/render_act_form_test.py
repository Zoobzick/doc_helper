"""Render the actual create template for scripts/test_act_form.cjs (no database needed)."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings.acts_test_settings")

import django

django.setup()

from django.template import engines
from django.test import RequestFactory
from acts_app.forms import ActForm, ActProjectsForm, ActMaterialFormSet, ActAttachmentCreateFormSet

request = RequestFactory().get("/acts/create/")
request.user = SimpleNamespace(pk=99)
source = (ROOT / "templates/acts_app/act_form.html").read_text(encoding="utf-8")
source = source.replace('{% extends "base.html" %}', "").replace("{{ block.super }}", "")
html = engines["django"].from_string(source).render({
    "request": request, "csrf_token": "test-csrf", "form": ActForm(),
    "projects_form": ActProjectsForm(),
    "material_formset": ActMaterialFormSet(prefix="mat", initial=[{}]),
    "attachment_formset": ActAttachmentCreateFormSet(prefix="att", act_number=""),
    "mode": "create", "selected_projects": [], "approval_items": [],
})
destination = Path(sys.argv[1])
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(html, encoding="utf-8")
print(destination)
