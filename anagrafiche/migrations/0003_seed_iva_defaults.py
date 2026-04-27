from django.db import migrations
from decimal import Decimal


ESIGIBILITA = [
    ('I', True),
    ('D', False),
    ('S', False),
]


POSIZIONI_IVA = [
    # descrizione, aliquota, natura, esente, reverse_charge, scissione, bollo
    ('IVA 22%', Decimal('22.00'), '', False, False, False, False),
    ('IVA 10%', Decimal('10.00'), '', False, False, False, False),
    ('IVA 5%',  Decimal('5.00'),  '', False, False, False, False),
    ('IVA 4%',  Decimal('4.00'),  '', False, False, False, False),
    ('Esente art. 10 DPR 633/72', Decimal('0.00'), 'N4', True, False, False, False),
    ('Non imponibile art. 8 DPR 633/72 - Esportazioni', Decimal('0.00'), 'N3.1', False, False, False, False),
    ('Non imponibile art. 41 DL 331/93 - Intra-UE', Decimal('0.00'), 'N3.2', False, False, False, False),
    ('Reverse charge - art. 17 c.6 DPR 633/72', Decimal('0.00'), 'N6.9', False, True, False, False),
    ('Fuori campo IVA - art. 15', Decimal('0.00'), 'N1', False, False, False, False),
]


def seed_iva(apps, schema_editor):
    EsigibilitaIva = apps.get_model('anagrafiche', 'EsigibilitaIva')
    PosizioneIva = apps.get_model('anagrafiche', 'PosizioneIva')

    for tipo, attiva in ESIGIBILITA:
        EsigibilitaIva.objects.get_or_create(tipo=tipo, defaults={'attiva': attiva})

    for (descr, aliq, natura, esente, rc, ss, bollo) in POSIZIONI_IVA:
        PosizioneIva.objects.get_or_create(
            descrizione=descr,
            defaults={
                'aliquota': aliq,
                'esigibilita_iva': 'I',
                'scissione_pagamenti': ss,
                'reverse_charge': rc,
                'bollo': bollo,
                'esente': esente,
                'natura': natura,
            },
        )


def unseed_iva(apps, schema_editor):
    EsigibilitaIva = apps.get_model('anagrafiche', 'EsigibilitaIva')
    PosizioneIva = apps.get_model('anagrafiche', 'PosizioneIva')
    EsigibilitaIva.objects.filter(tipo__in=[t for t, _ in ESIGIBILITA]).delete()
    PosizioneIva.objects.filter(
        descrizione__in=[p[0] for p in POSIZIONI_IVA]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('anagrafiche', '0002_seed_categorie_costo'),
    ]

    operations = [
        migrations.RunPython(seed_iva, reverse_code=unseed_iva),
    ]
