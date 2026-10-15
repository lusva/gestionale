"""Le fatture già segnate incassate prima degli incassi parziali: un incasso
per scadenza, dell'intero importo, alla data di pagamento della fattura (o
della scadenza se manca). Così il residuo è zero ovunque.

Gli incassi creati qui hanno la nota ``NOTA``: tornando indietro si tolgono
solo quelli."""
from django.db import migrations

NOTA = 'Registrato dalla migrazione 0010: fattura già segnata incassata.'


def avanti(apps, schema_editor):
    ScadenzaFattura = apps.get_model('documenti', 'ScadenzaFattura')
    IncassoScadenza = apps.get_model('documenti', 'IncassoScadenza')
    nuovi = []
    for s in (ScadenzaFattura.objects.filter(fattura__pagata=True, importo__gt=0)
              .select_related('fattura').iterator()):
        if IncassoScadenza.objects.filter(scadenza=s).exists():
            continue
        nuovi.append(IncassoScadenza(
            scadenza=s, importo=s.importo, data=s.fattura.data_pagamento or s.data, note=NOTA,
        ))
    IncassoScadenza.objects.bulk_create(nuovi, batch_size=500)


def indietro(apps, schema_editor):
    apps.get_model('documenti', 'IncassoScadenza').objects.filter(note=NOTA).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('documenti', '0009_incassi_scadenze'),
    ]

    operations = [
        migrations.RunPython(avanti, indietro),
    ]
