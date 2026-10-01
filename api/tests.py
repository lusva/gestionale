"""Test dell'API v2: lettura, anteprima, creazione fatture, idempotenza e permessi."""
import json
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now

from accounts.models import Ruolo
from anagrafiche.models import FormePagamento, PosizioneIva, Scadenza
from audit.models import AuditLog
from clienti.models import Cliente
from documenti.models import TestataFattura

from .models import ApiToken, RichiestaIdempotente


class ApiV2Base(TestCase):
    def setUp(self):
        User = get_user_model()
        self.utente = User.objects.create_user('niki', 'niki@example.com', 'x')
        self.utente.profile.ruolo = Ruolo.ACCOUNT_MANAGER
        self.utente.profile.save()
        self.token = ApiToken.objects.create(user=self.utente, name='NIKI', scrittura=True)
        self.iva22 = PosizioneIva.objects.create(descrizione='IVA 22%', aliquota=Decimal('22'))
        self.iva10 = PosizioneIva.objects.create(descrizione='IVA 10%', aliquota=Decimal('10'))
        self.bonifico30 = FormePagamento.objects.create(tipo_pagamento='Bonifico 30 gg')
        Scadenza.objects.create(forme_pagamento=self.bonifico30, numero_giorni=30,
                                percentuale=Decimal('100'))
        self.rossi = Cliente.objects.create(
            ragione_sociale='Rossi Impianti Srl', partita_iva='01234567890',
            posizione_iva=self.iva22, forme_pagamento=self.bonifico30)
        self.nudo = Cliente.objects.create(ragione_sociale='Bianchi', partita_iva='09876543210')

    def get(self, url, token=None):
        return self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {token or self.token.token_in_chiaro}')

    def post(self, url, dati, token=None, chiave=None):
        extra = {'HTTP_AUTHORIZATION': f'Bearer {token or self.token.token_in_chiaro}'}
        if chiave:
            extra['HTTP_IDEMPOTENCY_KEY'] = chiave
        return self.client.post(url, json.dumps(dati), content_type='application/json', **extra)

    def fattura(self, **altro):
        return {'cliente_id': self.rossi.pk, 'data_documento': '2026-09-30',
                'righe': [{'descrizione': 'Consulenza settembre', 'importo_unitario': '1200'}],
                **altro}


class LetturaTest(ApiV2Base):
    def test_senza_token(self):
        self.assertEqual(self.client.get('/api/v2/health').status_code, 401)

    def test_token_in_query_rifiutato(self):
        r = self.client.get(f'/api/v2/health?api_token={self.token.token_in_chiaro}')
        self.assertEqual(r.status_code, 401)

    def test_health(self):
        d = self.get('/api/v2/health').json()
        self.assertEqual(d['status'], 'ok')
        self.assertTrue(d['scrittura'])
        self.assertTrue(d['permessi']['documenti.modifica'])

    def test_clienti_ricerca_per_parole_con_predefiniti(self):
        d = self.get('/api/v2/clienti?q=rossi srl').json()
        self.assertEqual([c['id'] for c in d['results']], [self.rossi.pk])
        self.assertEqual(d['results'][0]['posizione_iva']['aliquota'], '22.00')
        self.assertEqual(d['results'][0]['forma_pagamento']['id'], self.bonifico30.pk)
        d = self.get('/api/v2/clienti?q=09876543210').json()
        self.assertEqual([c['id'] for c in d['results']], [self.nudo.pk])

    def test_tabelle(self):
        ids = {p['id'] for p in self.get('/api/v2/posizioni-iva').json()['results']}
        self.assertLessEqual({self.iva22.pk, self.iva10.pk}, ids)
        forme = self.get('/api/v2/forme-pagamento').json()['results']
        self.assertEqual(forme[0]['scadenze'][0]['giorni'], 30)


class AnteprimaTest(ApiV2Base):
    def test_calcola_senza_salvare(self):
        r = self.post('/api/v2/fatture?anteprima=1', self.fattura())
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(d['valori']['imponibile'], '1200.00')
        self.assertEqual(d['valori']['totale'], '1464.00')
        self.assertEqual(d['tipo_documento'], 'TD01')
        self.assertEqual(d['numero_previsto'], 1)
        self.assertEqual(d['scadenze'], [{'data': '2026-10-30', 'importo': '1464.00'}])
        self.assertFalse(TestataFattura.objects.exists())

    def test_anteprima_con_token_sola_lettura(self):
        lettura = ApiToken.objects.create(user=self.utente, name='ro')
        r = self.post('/api/v2/fatture?anteprima=1', self.fattura(), token=lettura.token_in_chiaro)
        self.assertEqual(r.status_code, 200)

    def test_errori_per_campo(self):
        r = self.post('/api/v2/fatture?anteprima=1', {
            'cliente_id': self.nudo.pk,
            'righe': [{'descrizione': '', 'importo_unitario': 'tanti'}, 'x']})
        self.assertEqual(r.status_code, 400)
        errori = r.json()['errori']
        self.assertIn('righe.0.descrizione', errori)
        self.assertIn('righe.0.importo_unitario', errori)
        self.assertIn('righe.0.iva_id', errori)      # il cliente non ha IVA predefinita
        self.assertIn('righe.1', errori)

    def test_totale_positivo(self):
        r = self.post('/api/v2/fatture?anteprima=1', self.fattura(
            righe=[{'descrizione': 'Sconto', 'importo_unitario': '-10'}]))
        self.assertEqual(r.status_code, 400)
        self.assertIn('righe', r.json()['errori'])


class CreaFatturaTest(ApiV2Base):
    def test_crea_con_predefiniti_del_cliente(self):
        r = self.post('/api/v2/fatture', self.fattura(
            righe=[{'descrizione': 'Consulenza', 'quantita': '2', 'importo_unitario': '500'},
                   {'descrizione': 'Libri', 'importo_unitario': '100', 'iva_id': self.iva10.pk}]))
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        f = TestataFattura.objects.get(pk=d['id'])
        self.assertEqual((f.numero, f.anno), (1, now().year))
        self.assertEqual(f.tipo_documento, 'TD01')
        self.assertEqual(f.sdi_stato, 'non_inviata')
        self.assertEqual(f.forme_pagamento, self.bonifico30)
        self.assertEqual(f.imponibile, Decimal('1100.00'))
        self.assertEqual(d['totale'], '1330.00')             # 1000 × 1,22 + 100 × 1,10
        self.assertEqual([r.iva for r in f.righe.all()], [self.iva22, self.iva10])
        self.assertEqual(list(f.scadenze.values_list('data', 'importo')),
                         [(date(2026, 10, 30), Decimal('1330.00'))])
        self.assertTrue(d['url'].endswith(f'/documenti/fatture/{f.pk}/'))
        audit = AuditLog.objects.filter(target_type='TestataFattura').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor_id, self.utente.pk)

    def test_idempotenza(self):
        a = self.post('/api/v2/fatture', self.fattura(), chiave='prop-1')
        b = self.post('/api/v2/fatture', self.fattura(), chiave='prop-1')
        self.assertEqual(a.status_code, 201)
        self.assertEqual(b.status_code, 201)
        self.assertEqual(b['Idempotent-Replayed'], 'true')
        self.assertEqual(a.json()['id'], b.json()['id'])
        self.assertEqual(TestataFattura.objects.count(), 1)
        c = self.post('/api/v2/fatture', self.fattura(), chiave='prop-2')
        self.assertEqual(c.json()['numero'], 2)

    def test_errore_non_ricordato(self):
        r = self.post('/api/v2/fatture', self.fattura(cliente_id=9999), chiave='k')
        self.assertEqual(r.status_code, 400)
        self.assertFalse(RichiestaIdempotente.objects.exists())
        r = self.post('/api/v2/fatture', self.fattura(), chiave='k')
        self.assertEqual(r.status_code, 201)

    def test_token_sola_lettura(self):
        lettura = ApiToken.objects.create(user=self.utente, name='ro')
        r = self.post('/api/v2/fatture', self.fattura(), token=lettura.token_in_chiaro)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(TestataFattura.objects.exists())

    def test_ruolo_visualizzatore(self):
        self.utente.profile.ruolo = Ruolo.VISUALIZZATORE
        self.utente.profile.save()
        r = self.post('/api/v2/fatture', self.fattura())
        self.assertEqual(r.status_code, 403)

    def test_elenco_e_dettaglio(self):
        creata = self.post('/api/v2/fatture', self.fattura()).json()
        d = self.get(f'/api/v2/fatture?cliente={self.rossi.pk}&pagata=0').json()
        self.assertEqual([f['id'] for f in d['results']], [creata['id']])
        self.assertEqual(self.get('/api/v2/fatture?pagata=1').json()['count'], 0)
        det = self.get(f"/api/v2/fatture/{creata['id']}").json()
        self.assertEqual(det['righe'][0]['descrizione'], 'Consulenza settembre')
        self.assertEqual(det['valori']['imposta'], '264.00')


class CreaClienteTest(ApiV2Base):
    DATI = {'ragione_sociale': 'Ronchetti Luca', 'tipo': 'privato', 'partita_iva': '02643290353',
            'codice_fiscale': 'RNCLCU80A01H223X', 'indirizzo': 'Via Roma 1', 'cap': '42019',
            'citta': 'Scandiano', 'provincia': 're', 'pec': 'luca@pec.it',
            'ref_nome': 'Luca', 'ref_cognome': 'Ronchetti', 'ref_email': 'luca@example.com'}

    def test_anteprima_non_salva(self):
        r = self.post('/api/v2/clienti?anteprima=1', self.DATI)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['dati']['partita_iva'], 'IT02643290353')
        self.assertEqual(r.json()['dati']['provincia'], 'RE')
        self.assertFalse(Cliente.objects.filter(ragione_sociale='Ronchetti Luca').exists())

    def test_crea_con_referente_e_idempotenza(self):
        a = self.post('/api/v2/clienti', self.DATI, chiave='cli-1')
        b = self.post('/api/v2/clienti', self.DATI, chiave='cli-1')
        self.assertEqual(a.status_code, 201, a.content)
        self.assertEqual(a.json()['id'], b.json()['id'])
        c = Cliente.objects.get(pk=a.json()['id'])
        self.assertEqual((c.tipo, c.stato, c.citta), ('privato', 'attivo', 'Scandiano'))
        self.assertEqual(c.contatti.get().email, 'luca@example.com')
        self.assertTrue(a.json()['url'].endswith(f'/clienti/{c.pk}/'))

    def test_errori_e_doppione(self):
        r = self.post('/api/v2/clienti?anteprima=1', {'ragione_sociale': 'X', 'partita_iva': '123', 'cap': 'abc'})
        self.assertEqual(r.status_code, 400)
        self.assertLessEqual({'partita_iva', 'cap'}, set(r.json()['errori']))
        r = self.post('/api/v2/clienti', {**self.DATI, 'partita_iva': 'IT01234567890'})
        self.assertEqual(r.status_code, 400)                  # P.IVA di Rossi: già presente
        self.assertIn('partita_iva', r.json()['errori'])

    def test_permesso(self):
        self.utente.profile.ruolo = Ruolo.VISUALIZZATORE
        self.utente.profile.save()
        self.assertEqual(self.post('/api/v2/clienti', self.DATI).status_code, 403)
