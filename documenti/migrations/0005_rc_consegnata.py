"""La ricevuta di consegna (RC) ora porta la fattura in ``accettata``:
le fatture rimaste in ``ricevuta_sdi`` (B2B/B2C, nessuna NE in arrivo)
vengono allineate."""
from django.db import migrations


def ricevuta_a_accettata(apps, schema_editor):
    TestataFattura = apps.get_model('documenti', 'TestataFattura')
    TestataFattura.objects.filter(sdi_stato='ricevuta_sdi').update(
        sdi_stato='accettata',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('documenti', '0004_numerazione_unica_iva_protect_sdi'),
    ]

    operations = [
        migrations.RunPython(ricevuta_a_accettata, migrations.RunPython.noop),
    ]
