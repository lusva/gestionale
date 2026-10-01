"""I token API si salvano come hash SHA-256 (più un prefisso non segreto);
le richieste idempotenti ricordano l'impronta del corpo."""
import hashlib

from django.db import migrations, models


def hash_token_esistenti(apps, schema_editor):
    """Il valore salvato finora era il token in chiaro: lo si sostituisce
    con il suo hash, così i token già distribuiti continuano a funzionare."""
    ApiToken = apps.get_model('api', 'ApiToken')
    for tok in ApiToken.objects.all():
        raw = tok.token_hash
        if len(raw) == 64 and all(c in '0123456789abcdef' for c in raw):
            continue                                    # già un hash
        tok.prefisso = raw[:12]
        tok.token_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        tok.save(update_fields=['token_hash', 'prefisso'])


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0002_token_scrittura_idempotenza'),
    ]

    operations = [
        migrations.RenameField(
            model_name='apitoken',
            old_name='token',
            new_name='token_hash',
        ),
        migrations.AlterField(
            model_name='apitoken',
            name='token_hash',
            field=models.CharField(editable=False, max_length=64, unique=True),
        ),
        migrations.AddField(
            model_name='apitoken',
            name='prefisso',
            field=models.CharField(blank=True, editable=False, help_text='Inizio del token, per riconoscerlo (non segreto).', max_length=12),
        ),
        migrations.RunPython(hash_token_esistenti, migrations.RunPython.noop),
        migrations.AddField(
            model_name='richiestaidempotente',
            name='hash_corpo',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AlterField(
            model_name='richiestaidempotente',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, db_index=True),
        ),
    ]
