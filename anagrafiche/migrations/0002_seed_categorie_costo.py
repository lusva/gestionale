from django.db import migrations


CATEGORIE = [
    ('MAT', 'Materie prime'),
    ('MERC', 'Merci / Prodotti finiti'),
    ('SERV', 'Servizi'),
    ('UTIL', 'Utenze'),
    ('CARB', 'Carburanti'),
    ('MANU', 'Manutenzioni'),
    ('CONS', 'Consulenze'),
    ('TRAS', 'Trasporti'),
    ('AFFI', 'Affitti'),
    ('ASSI', 'Assicurazioni'),
    ('CANC', 'Cancelleria'),
    ('PUBB', 'Pubblicità'),
    ('AMM', 'Ammortamenti'),
    ('PERS', 'Costi del personale'),
    ('ALTRO', 'Altro'),
]


def seed_categorie(apps, schema_editor):
    CategoriaCosto = apps.get_model('anagrafiche', 'CategoriaCosto')
    for i, (codice, descrizione) in enumerate(CATEGORIE):
        CategoriaCosto.objects.update_or_create(
            codice=codice,
            defaults={
                'descrizione': descrizione,
                'attiva': True,
                'ordinamento': i + 1,
            },
        )


def unseed_categorie(apps, schema_editor):
    CategoriaCosto = apps.get_model('anagrafiche', 'CategoriaCosto')
    CategoriaCosto.objects.filter(codice__in=[c for c, _ in CATEGORIE]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('anagrafiche', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_categorie, reverse_code=unseed_categorie),
    ]
