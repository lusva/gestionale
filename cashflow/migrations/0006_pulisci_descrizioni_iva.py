"""Le liquidazioni IVA stimate avevano «(stima)» e «— periodo in corso» nella
descrizione, rimasti anche dopo il pagamento o la modifica a mano: ora lo
stato lo dice un'etichetta (vedi cashflow/iva.py), il testo resta pulito."""
from django.db import migrations


def pulisci(apps, schema_editor):
    ScadenzaFiscale = apps.get_model('cashflow', 'ScadenzaFiscale')
    for s in ScadenzaFiscale.objects.exclude(periodo_iva=''):
        nuova = s.descrizione.replace(' — periodo in corso', '').replace(' (stima)', '').strip()
        if nuova != s.descrizione:
            ScadenzaFiscale.objects.filter(pk=s.pk).update(descrizione=nuova)


class Migration(migrations.Migration):

    dependencies = [
        ('cashflow', '0005_scadenzafiscale_periodo_iva_scadenzafiscale_stimata_and_more'),
    ]

    operations = [
        migrations.RunPython(pulisci, migrations.RunPython.noop),
    ]
