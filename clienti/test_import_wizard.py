"""Verifica il confine tra anteprima, conferma e importazione effettiva."""
import io
import csv
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from accounts.models import Ruolo
from audit.models import AuditLog, Azione
from .models import Cliente, EsitoImportazioneClienti, Settore
from .views import MAX_CSV_BYTES, SESSION_KEY, _csv_decode_and_parse


class ImportClientiWizardTest(TestCase):
    def setUp(self):
        Settore.objects.all().delete()
        self.user = get_user_model().objects.create_user(username='importatore')
        self.client.force_login(self.user)
        self.url = reverse('clienti:import_csv')
        self.existing = Cliente.objects.create(
            ragione_sociale='Alfa', partita_iva='12345678901', citta='Milano', note='Da conservare',
        )
        self.headers = ['ragione_sociale', 'partita_iva', 'citta', 'note', 'settore']
        self.rows = [
            ['Alfa aggiornata', 'IT12345678901', 'Roma', '', ''],
            ['Beta nuova', 'IT12345678902', 'Torino', '', 'Nuovo settore'],
            ['Errore', 'non-valida', '', '', 'Non creare'],
        ]

    def upload(self, headers=None, rows=None):
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(self.headers if headers is None else headers)
        writer.writerows(self.rows if rows is None else rows)
        file = SimpleUploadedFile('clienti.csv', out.getvalue().encode(), content_type='text/csv')
        return self.client.post(self.url, {'file': file})

    def preview(self):
        self.upload()
        payload = self.client.session[SESSION_KEY]
        return self.client.post(self.url, {
            'step': '2', 'upload_token': payload['token'],
            **{f'map__{h}': h for h in self.headers},
        })

    def confirm_data(self):
        payload = self.client.session[SESSION_KEY]
        return {'step': '3', 'upload_token': payload['token'], 'preview_token': payload['preview_token']}

    def test_upload_e_anteprima_non_scrivono_clienti_settori_o_audit(self):
        before = AuditLog.objects.count()
        response = self.preview()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['step'], 3)
        self.assertEqual(response.context['plan']['counts'], {'create': 1, 'update': 1, 'unchanged': 0, 'error': 1})
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertFalse(Settore.objects.exists())
        self.assertFalse(EsitoImportazioneClienti.objects.exists())
        self.assertEqual(AuditLog.objects.count(), before)
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.ragione_sociale, 'Alfa')

    def test_conferma_applica_solo_righe_valide_e_preserva_campi_vuoti(self):
        self.preview()
        response = self.client.post(self.url, self.confirm_data(), follow=True)
        self.assertEqual(len(response.redirect_chain), 1)
        self.assertTrue(response.context['done'])
        self.assertEqual((response.context['created'], response.context['updated'], response.context['skipped']), (1, 1, 1))
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.citta, 'Roma')
        self.assertEqual(self.existing.note, 'Da conservare')
        self.assertEqual(self.existing.partita_iva, '12345678901')
        self.assertEqual(list(Settore.objects.values_list('nome', flat=True)), ['Nuovo settore'])
        self.assertNotIn(SESSION_KEY, self.client.session)
        event = AuditLog.objects.get(azione=Azione.IMPORT)
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.meta, {'created': 1, 'updated': 1, 'unchanged': 0, 'skipped': 1})

    def test_doppio_invio_e_sessione_stantia_non_ripetono_import(self):
        self.preview()
        data = self.confirm_data()
        stale_payload = self.client.session[SESSION_KEY]
        first = self.client.post(self.url, data)
        before = AuditLog.objects.count()
        # Simula una risposta concorrente che ripristina il vecchio payload.
        session = self.client.session
        session[SESSION_KEY] = stale_payload
        session.save()
        second = self.client.post(self.url, data)
        self.assertEqual(first.url, second.url)
        self.assertEqual(Cliente.objects.count(), 2)
        self.assertEqual(AuditLog.objects.count(), before)
        self.assertEqual(EsitoImportazioneClienti.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(azione=Azione.IMPORT).count(), 1)

    def test_modifica_concorrente_impone_nuova_anteprima(self):
        self.preview()
        data = self.confirm_data()
        self.existing.citta = 'Napoli'
        self.existing.save()
        response = self.client.post(self.url, data)
        self.assertEqual(response.context['step'], 3)
        self.assertNotEqual(response.context['preview_token'], data['preview_token'])
        self.assertContains(response, 'I dati sono cambiati')
        self.assertFalse(EsitoImportazioneClienti.objects.exists())
        self.assertEqual(Cliente.objects.count(), 1)
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.citta, 'Napoli')
        self.assertEqual(self.client.post(self.url, self.confirm_data()).status_code, 302)
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.citta, 'Roma')

    def test_get_anteprima_cambiata_disabilita_conferma(self):
        self.preview()
        self.existing.citta = 'Napoli'
        self.existing.save()
        response = self.client.get(self.url, {'fase': 'anteprima'})
        self.assertTrue(response.context['preview_stale'])
        self.assertFalse(response.context['can_import'])
        self.assertFalse(EsitoImportazioneClienti.objects.exists())

    def test_token_anteprima_errato_non_importa(self):
        self.preview()
        data = {**self.confirm_data(), 'preview_token': 'alterato'}
        response = self.client.post(self.url, data)
        self.assertEqual(response.context['step'], 2)
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertFalse(EsitoImportazioneClienti.objects.exists())

    def test_prefetch_pagina_upload_non_invalida_anteprima(self):
        self.preview()
        before = self.confirm_data()
        self.client.get(self.url, HTTP_SEC_PURPOSE='prefetch')
        self.assertEqual(self.confirm_data(), before)
        self.assertEqual(self.client.post(self.url, before).status_code, 302)

    def test_seconda_scheda_non_conferma_un_file_diverso(self):
        self.preview()
        old = self.confirm_data()
        self.upload(rows=[['Gamma', 'IT12345678903', '', '', '']])
        response = self.client.post(self.url, old)
        self.assertContains(response, 'È stato caricato un altro file')
        self.assertEqual(Cliente.objects.count(), 1)

    def test_mapping_duplicato_rifiutato(self):
        self.upload()
        token = self.client.session[SESSION_KEY]['token']
        response = self.client.post(self.url, {
            'step': '2', 'upload_token': token,
            'map__ragione_sociale': 'ragione_sociale', 'map__partita_iva': 'partita_iva',
            'map__citta': 'ragione_sociale',
        })
        self.assertEqual(response.context['step'], 2)
        self.assertNotIn('preview_token', self.client.session[SESSION_KEY])
        self.assertContains(response, 'una sola colonna')

    def test_sessione_scaduta_non_importa(self):
        self.preview()
        data = self.confirm_data()
        session = self.client.session
        payload = session[SESSION_KEY]
        session[SESSION_KEY] = {**payload, 'uploaded_at': 0}
        session.save()
        response = self.client.post(self.url, data)
        self.assertContains(response, 'Il caricamento è scaduto')
        self.assertEqual(Cliente.objects.count(), 1)

    def test_righe_invariate_non_producono_salvataggi(self):
        self.rows = [['Alfa', 'IT12345678901', 'Milano', '', '']]
        response = self.preview()
        self.assertEqual(response.context['plan']['counts']['unchanged'], 1)
        self.assertFalse(response.context['can_import'])
        before = AuditLog.objects.count()
        self.client.post(self.url, self.confirm_data())
        self.assertEqual(AuditLog.objects.count(), before)
        self.assertFalse(EsitoImportazioneClienti.objects.exists())

    def test_errore_salvataggio_annulla_intero_batch_e_ricevuta(self):
        self.preview()
        data = self.confirm_data()
        before = AuditLog.objects.count()

        def fail(plan):
            Cliente.objects.create(ragione_sociale='Da annullare', partita_iva='IT00000000001')
            raise IntegrityError('errore simulato')

        with patch('clienti.importazione.esegui_piano', side_effect=fail):
            response = self.client.post(self.url, data)
        self.assertEqual(response.context['step'], 3)
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), before)
        self.assertFalse(EsitoImportazioneClienti.objects.exists())
        self.assertFalse(Settore.objects.exists())

    def test_errori_audit_annullano_clienti_e_ricevuta(self):
        self.preview()
        with patch('clienti.views.audit_log', side_effect=RuntimeError('audit fallito')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self.confirm_data())
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertFalse(Settore.objects.exists())
        self.assertFalse(EsitoImportazioneClienti.objects.exists())

    def test_esito_accessibile_solo_all_autore(self):
        self.preview()
        result = self.client.post(self.url, self.confirm_data())
        other = get_user_model().objects.create_user(username='altro')
        self.client.force_login(other)
        self.assertEqual(self.client.get(result.url).status_code, 404)
        self.assertEqual(self.client.get(self.url, {'esito': 'token-invalido'}).status_code, 404)

    def test_visualizzatore_non_puo_caricare_o_confermare(self):
        self.user.profile.ruolo = Ruolo.VISUALIZZATORE
        self.user.profile.save()
        response = self.upload()
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(self.client.post(self.url, {'step': '3'}).status_code, 302)

    def test_paginazione_e_filtro_errori_senza_salvataggi(self):
        self.rows = [[f'Cliente {i}', f'IT{i:011}', '', '', ''] for i in range(65)]
        self.rows.append(['Errore', 'x', '', '', ''])
        self.preview()
        second = self.client.get(self.url, {'fase': 'anteprima', 'page': '2'})
        self.assertEqual(len(second.context['preview_rows_plan']), 16)
        errors = self.client.get(self.url, {'fase': 'anteprima', 'azione': 'error'})
        self.assertEqual(len(errors.context['preview_rows_plan']), 1)
        self.assertEqual(errors.context['preview_rows_plan'][0]['action'], 'error')
        self.assertEqual(Cliente.objects.count(), 1)

    def test_esempio_scaricabile_e_nuovo_upload_non_valido_invalida_precedente(self):
        example = self.client.get(self.url, {'esempio': '1'})
        self.assertEqual(example.status_code, 200)
        headers, rows, _ = _csv_decode_and_parse(example.content)
        self.assertIn('partita_iva', headers)
        self.assertEqual(len(rows), 1)
        self.preview()
        self.upload(headers=['nome', 'nome'])
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_rifiuta_file_troppo_grandi_vuoti_e_intestazioni_ambigue(self):
        for raw in [b'x' * (MAX_CSV_BYTES + 1), b'', b'nome,piva\n', b'nome,NOME\na,b', b'nome,\na,b', b'a\x00,b']:
            with self.subTest(raw=raw[:30]):
                with self.assertRaises(ValueError):
                    _csv_decode_and_parse(raw)
        with self.assertRaises(ValueError):
            _csv_decode_and_parse(('a,b\n' + 'x,y\n' * 2001).encode())

    def test_encoding_e_separatori(self):
        for raw, delimiter in [('ragione_sociale;partita_iva\nCaffè;12345678901'.encode('latin-1'), ';'),
                               ('ragione_sociale\tpartita_iva\nCaffè\t12345678901'.encode('utf-8-sig'), '\t')]:
            headers, rows, detected = _csv_decode_and_parse(raw)
            self.assertEqual(detected, delimiter)
            self.assertEqual(rows[0][0], 'Caffè')

    def test_riga_corta_non_impedisce_di_riconoscere_separatore(self):
        for separator in (';', '\t', ','):
            raw = ('ragione_sociale|partita_iva|citta\nAlfa|12345678901|Roma\n'
                   'Riga rotta|12345678902\nBeta|12345678903|Milano').replace('|', separator)
            headers, rows, detected = _csv_decode_and_parse(raw.encode())
            self.assertEqual(detected, separator)
            self.assertEqual(len(headers), 3)
            self.assertEqual([len(row) for row in rows], [3, 2, 3])
