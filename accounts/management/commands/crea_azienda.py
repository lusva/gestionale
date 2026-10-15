"""Crea un ambiente aziendale vuoto e il suo amministratore applicativo."""
import getpass
import os
import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from accounts.models import Ruolo, StatoUtente
from anagrafiche.models import AnagraficaAzienda
from core_settings.models import Organizzazione
from tenancy.context import azienda_context
from tenancy.models import Azienda


class Command(BaseCommand):
    help = 'Crea un’azienda isolata e un amministratore, senza inviare email.'

    def add_arguments(self, parser):
        parser.add_argument('nome', help='Nome della nuova azienda.')
        parser.add_argument('--username', required=True, help='Identificativo di accesso univoco.')
        parser.add_argument('--email', default='', help='Email dell’amministratore.')
        parser.add_argument(
            '--password-env', metavar='NOME_VARIABILE',
            help='Leggi la password da questa variabile ambiente; altrimenti richiedila senza mostrarla.',
        )

    def handle(self, *args, **options):
        nome = options['nome'].strip()
        username = options['username'].strip()
        email = options['email'].strip().lower()
        if not nome or len(nome) > Azienda._meta.get_field('nome').max_length:
            raise CommandError('Il nome azienda è obbligatorio e deve rispettare la lunghezza consentita.')
        User = get_user_model()
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError('Username non disponibile.')
        user = User(username=username, email=email)
        try:
            user.full_clean(exclude=['password'])
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from exc

        env_name = options.get('password_env')
        if env_name:
            password = os.environ.get(env_name, '')
            if not password:
                raise CommandError('La variabile ambiente indicata non contiene una password.')
        else:
            if not sys.stdin.isatty():
                raise CommandError('Usa un terminale interattivo oppure --password-env NOME_VARIABILE.')
            password = getpass.getpass('Password amministratore: ')
            if password != getpass.getpass('Conferma password: '):
                raise CommandError('Le password non coincidono.')
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from exc

        try:
            with transaction.atomic():
                azienda = Azienda.objects.create(nome=nome, attiva=True)
                with azienda_context(azienda.pk):
                    user.set_password(password)
                    user.save()
                    profile = user.profile
                    profile.ruolo = Ruolo.AMMINISTRATORE
                    profile.stato = StatoUtente.ATTIVO
                    profile.save(update_fields=['ruolo', 'stato'])
                    AnagraficaAzienda.objects.create(ragione_sociale=nome)
                    Organizzazione.objects.create(nome=nome)
        except IntegrityError as exc:
            raise CommandError('Creazione non riuscita: identificativo già utilizzato. Nessun dato creato.') from exc

        self.stdout.write(self.style.SUCCESS(
            f'Azienda creata (ID {azienda.pk}); amministratore: {username}. Nessuna email inviata.',
        ))
