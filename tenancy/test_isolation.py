"""Regressioni sul confine azienda: stesso sito, dati e file separati."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Ruolo
from anagrafiche.models import AnagraficaAzienda
from attivita.forms import AttivitaForm
from attivita.models import Attivita
from clienti.forms import ClienteForm
from clienti.models import Cliente, Settore, Tag
from documenti.models import TestataOfferta
from opportunita.forms import OpportunitaForm
from opportunita.models import Opportunita, Stadio

from .context import azienda_context, current_azienda_id
from .models import Azienda
from .storage import TenantFileSystemStorage


class AziendaIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.azienda_a, _ = Azienda.objects.get_or_create(pk=1, defaults={'nome': 'Azienda Alfa'})
        cls.azienda_b = Azienda.objects.create(nome='Azienda Beta')
        for key, azienda, valore in (
            ('a', cls.azienda_a, '100.00'), ('b', cls.azienda_b, '900.00'),
        ):
            with azienda_context(azienda.pk):
                user = get_user_model().objects.create_user(
                    username=f'utente_{key}', password='test-password',
                    is_staff=True, is_superuser=(key == 'a'),
                )
                profile = user.profile
                profile.ruolo = Ruolo.AMMINISTRATORE
                profile.save()
                settore = Settore.objects.create(nome='Consulenza comune')
                tag = Tag.objects.create(nome='Etichetta comune')
                cliente = Cliente.objects.create(
                    ragione_sociale=f'Cliente riservato {key}', settore=settore,
                    partita_iva='IT12345678901', account_manager=user,
                )
                cliente.tags.add(tag)
                opportunita = Opportunita.objects.create(
                    cliente=cliente, titolo=f'Opportunità riservata {key}',
                    valore=Decimal(valore), stadio=Stadio.CHIUSA_WIN, owner=user,
                )
                attivita = Attivita.objects.create(
                    cliente=cliente, titolo=f'Attività riservata {key}',
                    data=timezone.now(), owner=user,
                )
                offerta = TestataOfferta.objects.create(
                    cliente=cliente, data_documento=date(2026, 1, 1),
                )
                anagrafica = AnagraficaAzienda.objects.create(ragione_sociale=f'Organizzazione {key}')
                for name, obj in (
                    ('user', user), ('settore', settore), ('tag', tag),
                    ('cliente', cliente), ('opportunita', opportunita),
                    ('attivita', attivita), ('offerta', offerta), ('anagrafica', anagrafica),
                ):
                    setattr(cls, f'{name}_{key}', obj)

    def setUp(self):
        self.client.force_login(self.user_a)

    def test_liste_e_csv_separati_anche_per_superuser(self):
        for user, own, other in (
            (self.user_a, self.cliente_a, self.cliente_b),
            (self.user_b, self.cliente_b, self.cliente_a),
        ):
            self.client.force_login(user)
            listing = self.client.get(reverse('clienti:list'))
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(list(listing.context['clienti']), [own])
            csv = self.client.get(reverse('clienti:export_csv'))
            self.assertContains(csv, own.ragione_sociale)
            self.assertNotContains(csv, other.ragione_sociale)

    def test_dettagli_documenti_e_modifiche_di_altra_azienda_non_accessibili(self):
        for route, obj in (
            ('clienti:detail', self.cliente_b), ('clienti:update', self.cliente_b),
            ('attivita:detail', self.attivita_b), ('attivita:update', self.attivita_b),
            ('opportunita:detail', self.opportunita_b),
            ('documenti:offerta_detail', self.offerta_b),
            ('documenti:offerta_pdf', self.offerta_b),
        ):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(route, args=[obj.pk])).status_code, 404)
        for route, obj in (
            ('clienti:delete', self.cliente_b), ('attivita:toggle', self.attivita_b),
        ):
            with self.subTest(route=route):
                self.assertEqual(self.client.post(reverse(route, args=[obj.pk])).status_code, 404)
        with azienda_context(self.azienda_b.pk):
            self.assertTrue(Cliente.objects.filter(pk=self.cliente_b.pk).exists())
            self.assertFalse(Attivita.objects.get(pk=self.attivita_b.pk).completata)

    def test_parametri_azienda_non_cambiano_identita(self):
        response = self.client.get(reverse('clienti:list'), {
            'azienda': self.azienda_b.pk, 'azienda_id': self.azienda_b.pk,
        }, HTTP_X_AZIENDA_ID=str(self.azienda_b.pk))
        self.assertEqual(list(response.context['clienti']), [self.cliente_a])

    def test_form_importati_prima_del_login_non_memorizzano_azienda(self):
        for azienda, own, other in (
            (self.azienda_a, self.cliente_a, self.cliente_b),
            (self.azienda_b, self.cliente_b, self.cliente_a),
        ):
            with self.subTest(azienda=azienda.pk), azienda_context(azienda.pk):
                for form in (OpportunitaForm(), AttivitaForm()):
                    choices = list(form.fields['cliente'].queryset)
                    self.assertIn(own, choices)
                    self.assertNotIn(other, choices)
                form = ClienteForm()
                self.assertEqual(
                    set(form.fields['settore'].queryset.values_list('azienda_id', flat=True)),
                    {azienda.pk},
                )
                self.assertEqual(form.fields['account_manager'].queryset.count(), 1)

    def test_post_relazioni_forgiate_viene_rifiutato(self):
        response = self.client.post(reverse('opportunita:create'), {
            'cliente': self.cliente_b.pk, 'titolo': 'Relazione vietata',
            'valore': '20', 'stadio': 'nuova', 'probabilita': 25,
            'owner': self.user_b.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('cliente', response.context['form'].errors)
        self.assertIn('owner', response.context['form'].errors)
        with azienda_context(self.azienda_a.pk):
            self.assertFalse(Opportunita.objects.filter(titolo='Relazione vietata').exists())

    def test_creazione_ignora_azienda_inviata_dal_browser(self):
        response = self.client.post(reverse('clienti:create'), {
            'ragione_sociale': 'Cliente nuovo locale', 'tipo': 'azienda',
            'nazione': 'IT', 'stato': 'prospect', 'azienda': self.azienda_b.pk,
            'azienda_id': self.azienda_b.pk, 'partita_iva': '12345678902',
        })
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['form'].errors)
        self.assertEqual(
            Cliente.all_objects.get(ragione_sociale='Cliente nuovo locale').azienda_id,
            self.azienda_a.pk,
        )

    def test_ricerca_e_report_non_aggregano_altre_aziende(self):
        for user, own, other, valore in (
            (self.user_a, self.cliente_a, self.cliente_b, '100.00'),
            (self.user_b, self.cliente_b, self.cliente_a, '900.00'),
        ):
            self.client.force_login(user)
            for route in ('search:cerca', 'search:rapida'):
                response = self.client.get(reverse(route), {'q': 'riservato'})
                self.assertEqual(response.status_code, 200)
                if route == 'search:cerca':
                    group = next(g for g in response.context['groups'] if g['kind'] == 'clienti')
                    self.assertEqual(list(group['items']), [own])
                else:
                    group = next(g for g in response.json()['gruppi'] if g['nome'] == 'Clienti')
                    self.assertEqual([item['titolo'] for item in group['voci']], [own.ragione_sociale])
            report = self.client.get(reverse('reports:index'))
            self.assertEqual(report.status_code, 200)
            self.assertEqual(report.context['kpi_ricavi'], Decimal(valore))
            self.assertEqual(report.context['kpi_nuovi_clienti'], 1)
            self.assertEqual([row['cliente'] for row in report.context['top5']], [own])

    def test_admin_scoped_anche_per_superuser(self):
        listing = self.client.get(reverse('admin:clienti_cliente_changelist'))
        self.assertContains(listing, self.cliente_a.ragione_sociale)
        self.assertNotContains(listing, self.cliente_b.ragione_sociale)
        foreign = self.client.get(reverse('admin:clienti_cliente_change', args=[self.cliente_b.pk]))
        self.assertIn(foreign.status_code, (302, 404))
        self.assertNotContains(self.client.get(reverse('admin:auth_user_changelist')), self.user_b.username)

    def test_numeri_documenti_e_codici_univoci_sono_per_azienda(self):
        self.assertEqual(self.offerta_a.numero, 1)
        self.assertEqual(self.offerta_b.numero, 1)
        self.assertEqual(self.settore_a.slug, self.settore_b.slug)
        self.assertEqual(self.cliente_a.partita_iva, self.cliente_b.partita_iva)

    def test_contesto_si_ripristina_dopo_richieste_e_eccezioni(self):
        original = current_azienda_id()
        self.client.force_login(self.user_b)
        self.client.get(reverse('clienti:list'))
        self.assertEqual(current_azienda_id(), original)
        with self.assertRaises(RuntimeError):
            with azienda_context(self.azienda_b.pk):
                raise RuntimeError('prova')
        self.assertEqual(current_azienda_id(), original)
        self.client.post(reverse('accounts:logout'))
        self.assertEqual(self.client.get(reverse('clienti:list')).status_code, 302)

    def test_senza_azienda_non_legge_ne_scrive(self):
        with azienda_context(None):
            self.assertFalse(Cliente.objects.exists())
            with self.assertRaises(PermissionDenied):
                Cliente.objects.create(ragione_sociale='Senza azienda')

    def test_modello_blocca_relazioni_e_modifiche_tra_aziende(self):
        with azienda_context(self.azienda_a.pk):
            with self.assertRaises(ValidationError):
                Opportunita.objects.create(cliente=self.cliente_b, titolo='Vietata')
            with self.assertRaises(ValidationError):
                Cliente.objects.filter(pk=self.cliente_a.pk).update(account_manager=self.user_b)
            with self.assertRaises(PermissionDenied):
                self.cliente_a.tags.add(self.tag_b)
            with self.assertRaises(PermissionDenied):
                self.cliente_b.save()
            with self.assertRaises(PermissionDenied):
                self.cliente_b.delete()
            with self.assertRaises(PermissionDenied):
                Cliente.objects.filter(pk=self.cliente_a.pk).update(azienda_id=self.azienda_b.pk)


class MediaIsolationTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(MEDIA_ROOT=self.tmp.name, MEDIA_PROTETTI_ACCEL='')
        override.enable()
        self.addCleanup(override.disable)
        self.azienda_a, _ = Azienda.objects.get_or_create(pk=1, defaults={'nome': 'Alfa'})
        self.azienda_b = Azienda.objects.create(nome='Beta')
        for key, azienda in (('a', self.azienda_a), ('b', self.azienda_b)):
            with azienda_context(azienda.pk):
                user = get_user_model().objects.create_user(username=f'media_{key}')
                setattr(self, f'user_{key}', user)
                logo_name = f'anagrafica/legacy-{key}.png'
                logo_path = Path(self.tmp.name) / logo_name
                logo_path.parent.mkdir(parents=True, exist_ok=True)
                from PIL import Image
                Image.new('RGB', (700, 300), '#8a23ab').save(logo_path)
                anagrafica = AnagraficaAzienda.objects.create(
                    ragione_sociale=key, logo=logo_name,
                    certificato_p12=f'certificati/{key}.p12',
                )
                setattr(self, f'anagrafica_{key}', anagrafica)
                profile = user.profile
                profile.avatar.save(f'{key}.png', ContentFile(b'avatar'), save=True)
                setattr(self, f'avatar_{key}', profile.avatar.name)
        self.client.force_login(self.user_a)

    def media_url(self, name):
        return reverse('media_protetto', kwargs={'percorso': name})

    def assert_file(self, name, status=200):
        response = self.client.get(self.media_url(name))
        self.assertEqual(response.status_code, status)
        response.close()
        return response

    def test_file_legacy_usa_proprieta_record(self):
        self.assert_file(self.anagrafica_a.logo.name)
        self.assert_file(self.anagrafica_b.logo.name, 404)
        self.client.force_login(self.user_b)
        self.assert_file(self.anagrafica_b.logo.name)
        self.assert_file(self.anagrafica_a.logo.name, 404)

    @override_settings(DEBUG=True)
    def test_debug_non_rende_pubblici_media(self):
        self.assert_file(self.avatar_b, 404)
        self.client.logout()
        self.assert_file(self.avatar_a, 302)

    def test_avatar_prefissati_e_file_non_referenziati(self):
        self.assertTrue(self.avatar_a.startswith(f'aziende/{self.azienda_a.pk}/avatars/'))
        self.assert_file(self.avatar_a)
        self.assert_file(self.avatar_b, 404)
        orphan = f'aziende/{self.azienda_a.pk}/avatars/orfano.png'
        (Path(self.tmp.name) / orphan).write_bytes(b'non collegato')
        self.assert_file(orphan, 404)
        self.assert_file(f'aziende/{self.azienda_b.pk}/../{self.azienda_a.pk}/avatars/a.png', 404)

    def test_certificati_mai_scaricabili(self):
        for name in ('certificati/a.p12', f'aziende/{self.azienda_a.pk}/certificati/a.p12'):
            target = Path(self.tmp.name) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'chiave privata')
            self.assert_file(name, 404)

    def test_copie_logo_separate_e_collegate_alla_proprieta(self):
        from anagrafiche.logo import LATO_SIDEBAR, logo_ridotto_nome
        names = []
        for azienda, anagrafica in (
            (self.azienda_a, self.anagrafica_a), (self.azienda_b, self.anagrafica_b),
        ):
            with azienda_context(azienda.pk):
                name = logo_ridotto_nome(anagrafica.logo, LATO_SIDEBAR)
                self.assertTrue(name.startswith(f'aziende/{azienda.pk}/anagrafica/ridotti/'))
                names.append(name)
        self.assert_file(names[0])
        self.assert_file(names[1], 404)
        with azienda_context(self.azienda_a.pk):
            self.anagrafica_a.logo = ''
            self.anagrafica_a.save(update_fields=['logo'])
        self.assert_file(names[0], 404)

    def test_anonimo_non_scarica_logo_o_avatar(self):
        self.client.logout()
        self.assert_file(self.anagrafica_a.logo.name, 302)
        self.assert_file(self.avatar_a, 302)

    @override_settings(MEDIA_PROTETTI_ACCEL='/media-protetti/')
    def test_x_accel_solo_dopo_autorizzazione(self):
        response = self.assert_file(self.avatar_a)
        self.assertEqual(response['X-Accel-Redirect'], '/media-protetti/' + self.avatar_a)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        response = self.assert_file(self.avatar_b, 404)
        self.assertNotIn('X-Accel-Redirect', response)

    def test_pdf_locale_non_legge_media_altra_azienda(self):
        from documenti.utils import _file_locale
        with azienda_context(self.azienda_a.pk):
            self.assertIsNotNone(_file_locale(self.media_url(self.anagrafica_a.logo.name)))
            self.assertIsNone(_file_locale(self.media_url(self.anagrafica_b.logo.name)))
            self.assertIsNone(_file_locale(self.media_url('certificati/a.p12')))

    def test_allegato_richiede_proprieta_e_permesso(self):
        from cashflow.models import RimborsoChilometrico
        with azienda_context(self.azienda_a.pk):
            rimborso = RimborsoChilometrico.objects.create(
                amministratore=self.user_a, data=timezone.localdate(),
                is_riepilogo_mensile=True, importo=Decimal('10'),
            )
            rimborso.allegato.save('ricevuta.txt', ContentFile(b'ricevuta'), save=True)
        self.assert_file(rimborso.allegato.name)
        with patch('gestionale.media_views.has_perm', return_value=False):
            self.assert_file(rimborso.allegato.name, 403)
        self.client.force_login(self.user_b)
        self.assert_file(rimborso.allegato.name, 404)

    def test_xml_e_allegati_fatture_richiedono_record_della_propria_azienda(self):
        from documenti.models import TestataFatturaAcquisto
        names = []
        for azienda in (self.azienda_a, self.azienda_b):
            with azienda_context(azienda.pk):
                fattura = TestataFatturaAcquisto.objects.create(data_documento=date(2026, 1, 1))
                fattura.xml_originale.save('fattura.xml', ContentFile(b'<fattura/>'), save=True)
                fattura.allegato.save('fattura.pdf', ContentFile(b'%PDF-test'), save=True)
                names.append((fattura.xml_originale.name, fattura.allegato.name))
        for name in names[0]:
            self.assert_file(name)
        for name in names[1]:
            self.assert_file(name, 404)
        self.client.force_login(self.user_b)
        for name in names[0]:
            self.assert_file(name, 404)
        for name in names[1]:
            self.assert_file(name)


class TenantStorageTests(SimpleTestCase):
    def test_prefix_duplicati_e_limite_nome(self):
        with TemporaryDirectory() as tmp, azienda_context(7):
            storage = TenantFileSystemStorage(location=tmp)
            first = storage.save('avatars/foto.png', ContentFile(b'prima'))
            second = storage.save(first, ContentFile(b'seconda'))
            self.assertEqual(first, 'aziende/7/avatars/foto.png')
            self.assertNotEqual(first, second)
            self.assertFalse(second.startswith('aziende/7/aziende/'))
            with storage.open(first) as saved:
                self.assertEqual(saved.read(), b'prima')
            long_name = storage.save('avatars/' + 'a' * 100 + '.png', ContentFile(b'x'), max_length=100)
            self.assertLessEqual(len(long_name), 100)
            with self.assertRaises(PermissionDenied):
                storage.save('aziende/8/avatars/foto.png', ContentFile(b'vietato'))
        with TemporaryDirectory() as tmp, azienda_context(None):
            storage = TenantFileSystemStorage(location=tmp)
            with self.assertRaises(PermissionDenied):
                storage.save('avatars/foto.png', ContentFile(b'anonimo'))
