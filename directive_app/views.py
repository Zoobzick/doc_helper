from django.http import FileResponse, Http404
from django.views.generic import CreateView, ListView, DeleteView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.auth.decorators import permission_required

from .models import Directive
from .forms import DirectiveForm


class DirectiveCreateView(PermissionRequiredMixin, CreateView):
    model = Directive
    form_class = DirectiveForm
    template_name = 'directive_app/add_directive.html'
    success_url = reverse_lazy('list_directive')

    permission_required = 'directive_app.create_directive_page'
    raise_exception = True


class DirectiveListView(PermissionRequiredMixin, ListView):
    model = Directive
    template_name = 'directive_app/list_directive.html'
    context_object_name = 'directives'
    paginate_by = 20

    permission_required = 'directive_app.view_directives_page'
    raise_exception = True

    def get_queryset(self):
        qs = super().get_queryset()
        q = (self.request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(number__icontains=q) | qs.filter(
                employee_full_name__icontains=q
            ) | qs.filter(
                employee_position__icontains=q
            ) | qs.filter(
                organization__icontains=q
            )
        return qs


class DirectiveDeleteView(PermissionRequiredMixin, DeleteView):
    model = Directive
    success_url = reverse_lazy('list_directive')

    permission_required = 'directive_app.delete_directive_page'
    raise_exception = True

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.pdf_file:
            obj.pdf_file.delete(save=False)
        return super().post(request, *args, **kwargs)


@permission_required('directive_app.open_directive_pdf', raise_exception=True)
def directive_open(request, pk: int):
    d = Directive.objects.filter(pk=pk).first()
    if not d:
        raise Http404("Приказ не найден")
    if not d.pdf_file:
        raise Http404("Файл не прикреплён")

    f = d.pdf_file.open('rb')
    filename = d.original_filename or d.pdf_file.name.split('/')[-1]

    response = FileResponse(f, as_attachment=False, filename=filename)
    response["Content-Type"] = "application/pdf"
    return response
