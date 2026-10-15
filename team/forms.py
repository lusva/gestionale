from django import forms

from accounts.models import Profile


class SchedaForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['qualifica', 'reparto', 'sede', 'telefono']
        labels = {'qualifica': 'Mansione', 'telefono': 'Telefono'}
        widgets = {
            'qualifica': forms.TextInput(attrs={'class': 'input', 'placeholder': 'Es. Commerciale'}),
            'reparto': forms.TextInput(attrs={'class': 'input', 'list': 'team-reparti',
                                              'placeholder': 'Es. Vendite'}),
            'sede': forms.TextInput(attrs={'class': 'input', 'placeholder': 'Es. Modena'}),
            'telefono': forms.TextInput(attrs={'class': 'input', 'type': 'tel'}),
        }
