from django import forms
from .models import Directive


class DirectiveForm(forms.ModelForm):
    class Meta:
        model = Directive
        fields = [
            'number',
            'date',
            'effective_date',
            'employee_full_name',
            'employee_position',
            'organization',
            'pdf_file',
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'effective_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'pdf_file': forms.FileInput(attrs={
                'class': 'form-control-file drag-drop-area',
                'accept': '.pdf,.doc,.docx'
            })
        }
