"""Crea un ambiente aziendale vuoto e il suo amministratore applicativo."""
import getpass
import os
import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from accounts.provisioning import crea_azienda_per_utente
from tenancy.models import Azienda


class Command(BaseCommand):
    help = 'Crea un’azienda isolata e un amministratore, senza inviare email.'

    def add_arguments(self, parser):
        parser.add_argument(
            'nome', nargs='?', default='',
            help='Nome azienda; se omesso lo inserirà l’amministratore al primo accesso.',
        )
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
        if len(nome) > Azienda._meta.get_field('nome').max_length:
            raise CommandError('Il nome azienda supera la lunghezza consentita.')
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
            user.set_password(password)
            azienda = crea_azienda_per_utente(user, nome)
        except IntegrityError as exc:
            raise CommandError('Creazione non riuscita: identificativo già utilizzato. Nessun dato creato.') from exc

        self.stdout.write(self.style.SUCCESS(
            f'Azienda creata (ID {azienda.pk}); amministratore: {username}. Nessuna email inviata.',
        ))
        if not azienda.configurata:
            self.stdout.write('L’utente inserirà i dati della propria azienda al primo accesso.')
