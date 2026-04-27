"""Promuove Cliente.settore da testo libero a ForeignKey verso il nuovo modello Settore.

Strategia:
1. Crea il modello Settore.
2. Aggiunge un campo temporaneo `settore_new` (FK) a Cliente.
3. Per ogni valore distinto di `settore` esistente, crea un Settore (o lo riusa)
   e collega il cliente a quel record.
4. Rimuove il vecchio campo `settore` (CharField).
5. Rinomina `settore_new` in `settore`.

Fixture iniziali: se il DB è vuoto, viene comunque popolato un set di settori
di default per avere subito una dropdown utilizzabile nel form cliente.
"""
from django.db import migrations, models


DEFAULT_SETTORI = [
    'Consulenza legale',
    'Consulenza fiscale',
    'F&B',
    'Retail',
    'Architettura',
    'Ingegneria',
    'Fashion',
    'Turismo',
    'Automotive',
    'IT & Software',
    'Manifattura',
    'Agricoltura',
    'Edilizia',
    'Marketing',
    'Formazione',
    'Sanità',
    'No profit',
    'Altro',
]


def forward(apps, schema_editor):
    Cliente = apps.get_model('clienti', 'Cliente')
    Settore = apps.get_model('clienti', 'Settore')

    from django.utils.text import slugify

    # Fixture di default
    for nome in DEFAULT_SETTORI:
        Settore.objects.get_or_create(
            nome=nome, defaults={'slug': slugify(nome)[:120]},
        )

    # Mappa testo → Settore per ogni cliente esistente
    for cliente in Cliente.objects.all():
        raw = (cliente.settore or '').strip()
        if not raw:
            continue
        settore, _ = Settore.objects.get_or_create(
            nome=raw, defaults={'slug': slugify(raw)[:120]},
        )
        cliente.settore_new = settore
        cliente.save(update_fields=['settore_new'])


def backward(apps, schema_editor):
    Cliente = apps.get_model('clienti', 'Cliente')
    for cliente in Cliente.objects.all():
        if cliente.settore_new_id:
            cliente.settore = cliente.settore_new.nome
            cliente.save(update_fields=['settore'])


class Migration(migrations.Migration):

    dependencies = [
        ('clienti', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Settore',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(max_length=100, unique=True)),
                ('slug', models.SlugField(blank=True, max_length=120, unique=True)),
            ],
            options={
                'ordering': ['nome'],
                'verbose_name': 'Settore',
                'verbose_name_plural': 'Settori',
            },
        ),
        # Rimuovi l'indice sul vecchio CharField per poter poi rimuovere il campo
        migrations.RemoveIndex(
            model_name='cliente',
            name='clienti_cli_settore_36716a_idx',
        ),
        migrations.AddField(
            model_name='cliente',
            name='settore_new',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.PROTECT,
                related_name='clienti_new',
                to='clienti.settore',
            ),
        ),
        migrations.RunPython(forward, backward),
        migrations.RemoveField(
            model_name='cliente',
            name='settore',
        ),
        migrations.RenameField(
            model_name='cliente',
            old_name='settore_new',
            new_name='settore',
        ),
        migrations.AlterField(
            model_name='cliente',
            name='settore',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.PROTECT,
                related_name='clienti',
                to='clienti.settore',
            ),
        ),
        migrations.AddIndex(
            model_name='cliente',
            index=models.Index(fields=['settore'], name='clienti_cli_settore_35dd39_idx'),
        ),
    ]
