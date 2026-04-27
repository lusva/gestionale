"""Seed the CRM with demo data that mirrors the design handoff mock.

Usage: `python manage.py seed [--reset]`
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Profile, Ruolo, StatoUtente
from attivita.models import Attivita, TipoAttivita
from clienti.models import Cliente, Contatto, Settore, StatoCliente, Tag, TipoCliente
from core_settings.models import Organizzazione
from opportunita.models import Opportunita, Stadio


USERS = [
    {'email': 'marco@studiorossi.it', 'first': 'Marco', 'last': 'Rossi', 'ruolo': Ruolo.AMMINISTRATORE, 'last_login_offset_minutes': 0, 'is_staff': True, 'is_super': True},
    {'email': 'giulia@studiorossi.it', 'first': 'Giulia', 'last': 'Ferrari', 'ruolo': Ruolo.ACCOUNT_MANAGER, 'last_login_offset_minutes': 12},
    {'email': 'lucia@studiorossi.it', 'first': 'Lucia', 'last': 'Bianchi', 'ruolo': Ruolo.ACCOUNT_MANAGER, 'last_login_offset_minutes': 60 * 2},
    {'email': 'stefano@studiorossi.it', 'first': 'Stefano', 'last': 'Conti', 'ruolo': Ruolo.VISUALIZZATORE, 'last_login_offset_minutes': 60 * 24},
    {'email': 'elena@studiorossi.it', 'first': 'Elena', 'last': 'Romano', 'ruolo': Ruolo.ACCOUNT_MANAGER, 'last_login_offset_minutes': 60 * 24 * 21, 'stato': StatoUtente.SOSPESO, 'is_active': False},
]


CLIENTI = [
    {'rs': 'Moretti & Associati', 'tipo': TipoCliente.AZIENDA, 'settore': 'Consulenza legale', 'citta': 'Milano', 'prov': 'MI', 'piva': 'IT02847593021', 'stato': StatoCliente.ATTIVO, 'fatt': 142000, 'am': 'giulia@studiorossi.it', 'contatto': ('Elena', 'Moretti', 'CEO', 'e.moretti@morettilex.it', '+39 02 4829 1742'), 'last': 3},
    {'rs': 'Caffè Brunelli SRL', 'tipo': TipoCliente.AZIENDA, 'settore': 'F&B', 'citta': 'Bologna', 'prov': 'BO', 'piva': 'IT01923847562', 'stato': StatoCliente.ATTIVO, 'fatt': 89500, 'am': 'lucia@studiorossi.it', 'contatto': ('Luca', 'Brunelli', 'Founder', 'luca@caffebrunelli.com', '+39 051 234 8765'), 'last': 1},
    {'rs': 'Studio Arch. Ferrari', 'tipo': TipoCliente.AZIENDA, 'settore': 'Architettura', 'citta': 'Torino', 'prov': 'TO', 'piva': 'IT03847592018', 'stato': StatoCliente.PROSPECT, 'fatt': 210400, 'am': 'marco@studiorossi.it', 'contatto': ('Giulia', 'Ferrari', 'Socio', 'giulia@studioferrari.eu', '+39 011 567 2918'), 'last': 7},
    {'rs': 'Panificio Conti', 'tipo': TipoCliente.PRIVATO, 'settore': 'Retail', 'citta': 'Parma', 'prov': 'PR', 'piva': 'IT04958273019', 'stato': StatoCliente.ATTIVO, 'fatt': 34200, 'am': 'giulia@studiorossi.it', 'contatto': ('Marco', 'Conti', 'Titolare', 'info@panificioconti.it', '+39 0521 123 456'), 'last': 0},
    {'rs': 'Tecnomare Ingegneria', 'tipo': TipoCliente.AZIENDA, 'settore': 'Ingegneria', 'citta': 'Genova', 'prov': 'GE', 'piva': 'IT05748392017', 'stato': StatoCliente.ATTIVO, 'fatt': 487000, 'am': 'marco@studiorossi.it', 'contatto': ('Paolo', 'Venturi', 'CEO', 'p.venturi@tecnomare.it', '+39 010 284 5912'), 'last': 2, 'contatti_extra': [('Chiara', 'Neri', 'CTO', 'c.neri@tecnomare.it', '+39 010 284 5914', False), ('Davide', 'Russo', 'Procurement', 'd.russo@tecnomare.it', '+39 010 284 5920', False)]},
    {'rs': 'Boutique Sofia', 'tipo': TipoCliente.PRIVATO, 'settore': 'Fashion', 'citta': 'Firenze', 'prov': 'FI', 'piva': 'IT06839472916', 'stato': StatoCliente.INATTIVO, 'fatt': 67300, 'am': 'lucia@studiorossi.it', 'contatto': ('Sofia', 'De Luca', 'Titolare', 'sofia@boutiquesofia.com', '+39 055 789 1234'), 'last': 60},
    {'rs': 'Agriturismo Le Querce', 'tipo': TipoCliente.AZIENDA, 'settore': 'Turismo', 'citta': 'Siena', 'prov': 'SI', 'piva': 'IT07928374815', 'stato': StatoCliente.ATTIVO, 'fatt': 128900, 'am': 'marco@studiorossi.it', 'contatto': ('Marta', 'Ricci', 'Direttrice', 'info@lequerce.it', '+39 0577 654 321'), 'last': 4},
    {'rs': 'Bianchi Automotive', 'tipo': TipoCliente.AZIENDA, 'settore': 'Automotive', 'citta': 'Modena', 'prov': 'MO', 'piva': 'IT08382947162', 'stato': StatoCliente.PROSPECT, 'fatt': 356000, 'am': 'giulia@studiorossi.it', 'contatto': ('Andrea', 'Bianchi', 'CEO', 'a.bianchi@bianchiauto.it', '+39 059 234 7890'), 'last': 1},
]


OPPORTUNITA = [
    # (cliente_rs, titolo, valore, stadio, prob, owner_email, chiusura_offset_days)
    ('Tecnomare Ingegneria', 'Audit software gestione cantieri', 52000, Stadio.NEGOZIAZIONE, 75, 'marco@studiorossi.it', 21),
    ('Tecnomare Ingegneria', 'Manutenzione evolutiva CRM', 45000, Stadio.NUOVA, 25, 'marco@studiorossi.it', 60),
    ('Bianchi Automotive', 'Modulo analisi carrello', 28000, Stadio.NUOVA, 30, 'giulia@studiorossi.it', 45),
    ('Studio Arch. Ferrari', 'Consulenza digitale BIM', 12500, Stadio.NUOVA, 30, 'marco@studiorossi.it', 30),
    ('Moretti & Associati', 'Integrazione e-signature', 18000, Stadio.QUALIFICATA, 45, 'giulia@studiorossi.it', 40),
    ('Caffè Brunelli SRL', 'Piano formazione staff', 9500, Stadio.QUALIFICATA, 50, 'lucia@studiorossi.it', 20),
    ('Agriturismo Le Querce', 'Nuovo sito + booking engine', 32000, Stadio.PROPOSTA, 55, 'marco@studiorossi.it', 18),
    ('Panificio Conti', 'Gestionale punto vendita', 6800, Stadio.PROPOSTA, 40, 'giulia@studiorossi.it', 25),
    ('Boutique Sofia', 'Campagna adv social', 15200, Stadio.PROPOSTA, 35, 'lucia@studiorossi.it', 35),
    ('Moretti & Associati', 'Consulenza GDPR annuale', 24500, Stadio.NEGOZIAZIONE, 70, 'giulia@studiorossi.it', 10),
    # Close-won historical (per KPI fatturato totale)
    ('Caffè Brunelli SRL', 'Fornitura pacchetto 2025', 14800, Stadio.CHIUSA_WIN, 100, 'lucia@studiorossi.it', -45),
    ('Tecnomare Ingegneria', 'Roll-out portale clienti', 210000, Stadio.CHIUSA_WIN, 100, 'marco@studiorossi.it', -30),
    ('Moretti & Associati', 'Fatturazione elettronica setup', 32000, Stadio.CHIUSA_WIN, 100, 'giulia@studiorossi.it', -60),
    ('Agriturismo Le Querce', 'Pacchetto marketing 2026', 28000, Stadio.CHIUSA_WIN, 100, 'marco@studiorossi.it', -5),
    ('Bianchi Automotive', 'Analisi e implementazione CMS', 185000, Stadio.CHIUSA_WIN, 100, 'giulia@studiorossi.it', -15),
    ('Caffè Brunelli SRL', 'Consulenza fornitura 2024', 54000, Stadio.CHIUSA_LOST, 0, 'lucia@studiorossi.it', -90),
]


ATTIVITA = [
    # (cliente_rs, tipo, titolo, data_offset_days, completata, owner_email)
    ('Tecnomare Ingegneria', TipoAttivita.CALL, 'Call di follow-up con Paolo Venturi', 1, False, 'marco@studiorossi.it'),
    ('Tecnomare Ingegneria', TipoAttivita.EMAIL, 'Invio preventivo revisionato', 4, False, 'marco@studiorossi.it'),
    ('Tecnomare Ingegneria', TipoAttivita.MEETING, 'Meeting in sede Genova', 11, False, 'marco@studiorossi.it'),
    ('Moretti & Associati', TipoAttivita.CALL, 'Chiamata Elena Moretti', -2, False, 'giulia@studiorossi.it'),
    ('Agriturismo Le Querce', TipoAttivita.EMAIL, 'Invio proposta revisionata', -1, False, 'marco@studiorossi.it'),
    ('Caffè Brunelli SRL', TipoAttivita.TASK, 'Verifica contratto fornitura', 0, True, 'lucia@studiorossi.it'),
    ('Bianchi Automotive', TipoAttivita.NOTE, 'Nota: da richiamare dopo il lancio', 0, False, 'giulia@studiorossi.it'),
]


class Command(BaseCommand):
    help = 'Popola il database con dati demo per il gestionale CRM.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Elimina prima tutti i dati CRM (clienti, opp, att, utenti non-admin)')

    @transaction.atomic
    def handle(self, *args, **opts):
        if opts['reset']:
            self.stdout.write('Reset dati esistenti…')
            Attivita.objects.all().delete()
            Opportunita.objects.all().delete()
            Contatto.objects.all().delete()
            Cliente.objects.all().delete()
            Tag.objects.all().delete()
            User.objects.exclude(is_superuser=True).delete()

        # Organization
        org, _ = Organizzazione.objects.get_or_create(defaults={'nome': 'Studio Rossi SRL', 'piano': 'Pro', 'posti_totali': 8})
        org.nome = 'Studio Rossi SRL'
        org.save()

        # Tags
        for name, color in [('Enterprise', 'info'), ('Nord Italia', 'info'), ('Centro', 'warn'), ('Sud', 'success'), ('VIP', 'danger')]:
            Tag.objects.get_or_create(nome=name, defaults={'colore': color})

        # Users + profiles
        now = timezone.now()
        user_map: dict[str, User] = {}
        for u in USERS:
            user, created = User.objects.get_or_create(
                username=u['email'],
                defaults={
                    'email': u['email'], 'first_name': u['first'], 'last_name': u['last'],
                    'is_staff': u.get('is_staff', False),
                    'is_superuser': u.get('is_super', False),
                    'is_active': u.get('is_active', True),
                },
            )
            if created:
                user.set_password('demo1234')
                user.save()
            user.last_login = now - dt.timedelta(minutes=u['last_login_offset_minutes'])
            user.save(update_fields=['last_login'])
            profile = user.profile
            profile.ruolo = u['ruolo']
            profile.stato = u.get('stato', StatoUtente.ATTIVO)
            profile.save()
            user_map[u['email']] = user

        self.stdout.write(self.style.SUCCESS(f'Utenti: {len(user_map)} (password demo: "demo1234")'))

        # Clienti
        cliente_map: dict[str, Cliente] = {}
        for data in CLIENTI:
            settore_obj, _ = Settore.objects.get_or_create(nome=data['settore'])
            c, _ = Cliente.objects.update_or_create(
                partita_iva=data['piva'],
                defaults={
                    'ragione_sociale': data['rs'],
                    'tipo': data['tipo'],
                    'settore': settore_obj,
                    'citta': data['citta'],
                    'provincia': data['prov'],
                    'nazione': 'IT',
                    'stato': data['stato'],
                    'account_manager': user_map.get(data['am']),
                    'ultimo_contatto': now - dt.timedelta(days=data['last']),
                    'cliente_dal': (now - dt.timedelta(days=400 + data['last'])).date(),
                    'cap': '00000',
                    'indirizzo': 'Via Esempio 1',
                    'pec': f'{data["rs"].split()[0].lower()}@pec.it',
                },
            )
            nome, cognome, ruolo, email, tel = data['contatto']
            Contatto.objects.update_or_create(
                cliente=c, email=email,
                defaults={'nome': nome, 'cognome': cognome, 'ruolo': ruolo, 'telefono': tel, 'primary': True},
            )
            for extra in data.get('contatti_extra', []):
                n, co, r, e, t, _prim = extra
                Contatto.objects.update_or_create(
                    cliente=c, email=e,
                    defaults={'nome': n, 'cognome': co, 'ruolo': r, 'telefono': t, 'primary': False},
                )
            cliente_map[data['rs']] = c

        self.stdout.write(self.style.SUCCESS(f'Clienti: {len(cliente_map)}'))

        # Opportunità
        opp_count = 0
        for rs, titolo, valore, stadio, prob, owner, offset in OPPORTUNITA:
            cliente = cliente_map.get(rs)
            if not cliente:
                continue
            chiusura = (now + dt.timedelta(days=offset)).date() if offset >= 0 else None
            ingresso = now - dt.timedelta(days=abs(offset) if stadio == Stadio.CHIUSA_WIN or stadio == Stadio.CHIUSA_LOST else 5)
            updated = now + dt.timedelta(days=offset) if offset < 0 else now
            o, created = Opportunita.objects.get_or_create(
                cliente=cliente, titolo=titolo,
                defaults={
                    'valore': Decimal(valore),
                    'stadio': stadio,
                    'probabilita': prob,
                    'chiusura_prevista': chiusura,
                    'owner': user_map.get(owner),
                    'ingresso_stadio': ingresso,
                },
            )
            if not created:
                o.valore = Decimal(valore)
                o.stadio = stadio
                o.probabilita = prob
                o.chiusura_prevista = chiusura
                o.owner = user_map.get(owner)
                o.ingresso_stadio = ingresso
                o.save()
            # Fudge updated_at for historical wins so dashboard fatturato per mese si popoli
            if stadio == Stadio.CHIUSA_WIN and offset < 0:
                Opportunita.objects.filter(pk=o.pk).update(updated_at=updated)
            opp_count += 1

        self.stdout.write(self.style.SUCCESS(f'Opportunità: {opp_count}'))

        # Attività
        att_count = 0
        for rs, tipo, titolo, offset, completata, owner in ATTIVITA:
            cliente = cliente_map.get(rs)
            if not cliente:
                continue
            Attivita.objects.update_or_create(
                cliente=cliente, titolo=titolo,
                defaults={
                    'tipo': tipo,
                    'data': now + dt.timedelta(days=offset),
                    'completata': completata,
                    'owner': user_map.get(owner),
                },
            )
            att_count += 1
        self.stdout.write(self.style.SUCCESS(f'Attività: {att_count}'))

        self.stdout.write(self.style.SUCCESS(
            f'\nSeed completato!\n'
            f'Login:   marco@studiorossi.it   (amministratore)\n'
            f'Password: demo1234\n\n'
            f'Superuser: usa `createsuperuser` se preferisci Django admin separato.'
        ))
