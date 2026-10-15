"""Test del server MCP: protocollo, permessi, anteprima che non lascia tracce,
idempotenza e i tool principali di ogni area."""
import json
from datetime import timedelta

from django.utils.timezone import localdate, now

from accounts.models import Ruolo
from attivita.models import Attivita
from cashflow.models import RimborsoChilometrico, ScadenzaFiscale, SpesaRicorrente
from documenti.models import (
    TestataDdt,
    TestataFattura,
    TestataNotaCredito,
    TestataOfferta,
    TestataOrdine,
)
from opportunita.models import Opportunita

from .models import ApiToken, Webhook
from .tests import ApiV2Base


class McpBase(ApiV2Base):
    def rpc(self, metodo, params=None, token=None, mid=1):
        r = self.client.post('/mcp', json.dumps({'jsonrpc': '2.0', 'id': mid, 'method': metodo,
                                                 'params': params or {}}),
                             content_type='application/json',
                             HTTP_AUTHORIZATION=f'Bearer {token or self.token.token_in_chiaro}')
        return r

    def tool(self, nome, token=None, **argomenti):
        r = self.rpc('tools/call', {'name': nome, 'arguments': argomenti}, token=token)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['result']


class ProtocolloTest(McpBase):
    def test_initialize_e_elenco(self):
        r = self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                    'clientInfo': {'name': 'test', 'version': '1'}})
        d = r.json()['result']
        self.assertEqual(d['protocolVersion'], '2025-06-18')
        self.assertTrue(d['_meta']['scrittura'])
        nomi = {t['name'] for t in self.rpc('tools/list').json()['result']['tools']}
        self.assertLessEqual({'crea_fattura', 'crea_ddt', 'converti_documento', 'scadenziario_attivo',
                              'crea_rimborso_km'}, nomi)
        crea = next(t for t in self.rpc('tools/list').json()['result']['tools'] if t['name'] == 'crea_fattura')
        self.assertFalse(crea['annotations']['readOnlyHint'])
        self.assertIn('anteprima', crea['inputSchema']['properties'])

    def test_notifica_e_senza_token(self):
        r = self.client.post('/mcp', json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}),
                             content_type='application/json',
                             HTTP_AUTHORIZATION=f'Bearer {self.token.token_in_chiaro}')
        self.assertEqual(r.status_code, 202)
        self.assertEqual(self.client.post('/mcp', '{}', content_type='application/json').status_code, 401)
        self.assertEqual(self.client.get('/mcp').status_code, 405)
        self.assertEqual(self.rpc('tools/call', {'name': 'boh'}).json()['error']['code'], -32602)

    def test_token_sola_lettura_non_vede_le_scritture(self):
        lettura = ApiToken.objects.create(user=self.utente, name='ro')
        nomi = {t['name'] for t in self.rpc('tools/list', token=lettura.token_in_chiaro).json()['result']['tools']}
        self.assertIn('elenco_documenti', nomi)
        self.assertNotIn('crea_fattura', nomi)
        r = self.tool('crea_fattura', token=lettura.token_in_chiaro, cliente='Rossi',
                      righe=[{'descrizione': 'x', 'prezzo': 10}])
        self.assertTrue(r['isError'])
        self.assertEqual(r['structuredContent']['errore'], 'token_sola_lettura')

    def test_visualizzatore_vede_solo_letture(self):
        self.utente.profile.ruolo = Ruolo.VISUALIZZATORE
        self.utente.profile.save()
        nomi = {t['name'] for t in self.rpc('tools/list').json()['result']['tools']}
        self.assertIn('cerca_clienti', nomi)
        self.assertFalse(any(n.startswith('crea_') for n in nomi))


class DocumentiTest(McpBase):
    def test_anteprima_non_lascia_tracce(self):
        Webhook.objects.create(name='w', url='http://127.0.0.1:9/', eventi=[], attivo=True)
        r = self.tool('crea_fattura', cliente='rossi impianti', anteprima=True,
                      righe=[{'descrizione': 'Consulenza', 'quantita': 2, 'prezzo': 500}])
        self.assertFalse(r['isError'], r)
        d = r['structuredContent']
        self.assertTrue(d['anteprima'])
        self.assertEqual((d['etichetta'], d['totale']), (f'Fattura 1/{now().year}', '1220.00'))
        self.assertEqual(d['scadenze'][0]['importo'], '1220.00')
        self.assertFalse(TestataFattura.objects.exists())
        self.assertIsNone(Webhook.objects.get().ultimo_status)      # nessun webhook partito

    def test_crea_idempotente(self):
        args = dict(cliente='Rossi', chiave='k1', righe=[{'descrizione': 'Consulenza', 'prezzo': 100}])
        a = self.tool('crea_fattura', **args)['structuredContent']
        b = self.tool('crea_fattura', **args)['structuredContent']
        self.assertEqual(a['id'], b['id'])
        self.assertTrue(b['ripetuta'])
        self.assertEqual(TestataFattura.objects.count(), 1)

    def test_errori_leggibili(self):
        r = self.tool('crea_fattura', cliente='Nessuno', righe=[{'descrizione': 'x', 'prezzo': 1}])
        self.assertTrue(r['isError'])
        self.assertIn('Nessun cliente corrisponde a «Nessuno»', r['structuredContent']['errori']['cliente'])
        r = self.tool('crea_fattura', cliente='Bianchi', righe=[{'descrizione': 'x', 'prezzo': 1}])
        self.assertIn('righe.0.iva_id', r['structuredContent']['errori'])       # Bianchi senza IVA
        r = self.tool('crea_fattura', cliente='Bianchi', righe=[{'descrizione': 'x', 'prezzo': 1,
                                                                 'aliquota_iva': 10}])
        self.assertFalse(r['isError'], r)
        self.assertEqual(r['structuredContent']['iva'][0]['descrizione'], 'IVA 10%')

    def test_ciclo_completo(self):
        off = self.tool('crea_offerta', cliente='Rossi', scadenza='2026-12-31',
                        righe=[{'descrizione': 'Impianto', 'quantita': 1, 'prezzo': 1000}])['structuredContent']
        self.assertEqual(off['etichetta'], f'Offerta 1/{now().year}')
        ordine = self.tool('converti_documento', conversione='offerta_in_ordine',
                           numero=1)['structuredContent']
        self.assertEqual(TestataOfferta.objects.get().stato, TestataOfferta.StatoOfferta.CONFERMATA)
        ddt = self.tool('converti_documento', conversione='ordine_in_ddt', numero=ordine['numero'])
        self.assertFalse(ddt['isError'], ddt)
        fatt = self.tool('converti_documento', conversione='ordine_in_fattura',
                         numero=ordine['numero'])['structuredContent']
        self.assertEqual(fatt['totale'], '1220.00')
        self.assertEqual(len(fatt['scadenze']), 1)                   # dalla forma di pagamento
        # prima della nota di credito: una nota totale salda la fattura (compensazione)
        pagata = self.tool('segna_fattura_pagata', numero=fatt['numero'], data_pagamento='2026-10-05')
        self.assertTrue(pagata['structuredContent']['pagata'])
        again = self.tool('segna_fattura_pagata', numero=fatt['numero'])
        self.assertTrue(again['isError'])
        nc = self.tool('converti_documento', conversione='fattura_in_nota_credito',
                       numero=fatt['numero'])['structuredContent']
        self.assertEqual(nc['etichetta'], f'Nota di credito NC/1/{now().year}')   # sezionale NC predefinito
        self.assertEqual([m.objects.count() for m in (TestataOrdine, TestataDdt, TestataFattura,
                                                      TestataNotaCredito)], [1, 1, 1, 1])

    def test_ddt_senza_prezzi_e_letture(self):
        r = self.tool('crea_ddt', cliente='Rossi', causale_trasporto='Vendita', porto='Franco',
                      righe=[{'descrizione': 'Pallet materiale', 'quantita': 3}])
        self.assertFalse(r['isError'], r)
        r = self.tool('crea_ddt', cliente='Bianchi', anteprima=True,       # senza IVA e senza prezzi
                      righe=[{'descrizione': 'Campionario', 'quantita': 1}])
        self.assertFalse(r['isError'], r)
        self.tool('crea_fattura', cliente='Rossi', righe=[{'descrizione': 'x', 'prezzo': 100}])
        el = self.tool('elenco_documenti', tipo='fattura', pagata=False)['structuredContent']
        self.assertEqual(el['conteggio'], 1)
        sc = self.tool('scadenziario_attivo')['structuredContent']
        self.assertEqual(sc['totali']['totale'], '122.00')
        det = self.tool('dettaglio_documento', tipo='ddt', numero=1)['structuredContent']
        self.assertEqual(det['righe'][0]['quantita'], '3.00')
        f = self.tool('fatturato')['structuredContent']
        self.assertEqual(f['totale_netto'], '100.00')
        scheda = self.tool('scheda_cliente', cliente='Rossi')['structuredContent']
        self.assertEqual(scheda['da_incassare'], '122.00')


class CrmCashflowTest(McpBase):
    def test_crm(self):
        r = self.tool('crea_attivita', titolo='Chiamare Rossi', tipo='call', data='2026-10-02T10:30',
                      cliente='Rossi')
        self.assertFalse(r['isError'], r)
        self.assertEqual(Attivita.objects.get().owner, self.utente)
        o = self.tool('crea_opportunita', titolo='Nuovo impianto', cliente='Rossi', valore=15000)
        self.assertFalse(o['isError'], o)
        s = self.tool('sposta_opportunita', opportunita='impianto', stadio='chiusa_win')
        self.assertEqual(Opportunita.objects.get().stadio, 'chiusa_win')
        self.assertIn('Chiusa vinta', s['structuredContent']['messaggio'])

    def test_cashflow(self):
        self.tool('crea_scadenza_fiscale', data_scadenza='2026-10-16', tipo='iva',
                  descrizione='Liquidazione IVA settembre', importo=2300)
        p = self.tool('segna_scadenza_fiscale_pagata', data_scadenza='2026-10-16', tipo='iva')
        self.assertFalse(p['isError'], p)
        self.assertTrue(ScadenzaFiscale.objects.get().pagata)
        s = self.tool('crea_spesa_ricorrente', descrizione='Affitto ufficio', importo=800,
                      data_inizio=str(localdate()))
        self.assertGreaterEqual(s['structuredContent']['scadenze_generate'], 12)
        self.assertEqual(SpesaRicorrente.objects.count(), 1)
        r = self.tool('crea_rimborso_km', partenza='Scandiano', destinazione='Milano', km=180,
                      tariffa_km=0.5)
        self.assertEqual(r['structuredContent']['importo'], '90.00')
        self.assertEqual(RimborsoChilometrico.objects.get().amministratore, self.utente)
        el = self.tool('scadenze_fiscali', da=str(localdate()),
                       a=str(localdate() + timedelta(days=40)))['structuredContent']
        self.assertGreaterEqual(el['conteggio'], 1)

    def test_rimborsi_km(self):
        r = self.tool('crea_rimborso_km', partenza='Scandiano', destinazione='Bologna', km=60,
                      andata_e_ritorno=True, tariffa_km=0.45, cliente='Rossi', anteprima=True)
        self.assertEqual(r['structuredContent']['importo'], '54.00')
        self.assertFalse(RimborsoChilometrico.objects.exists())
        self.tool('crea_rimborso_km', partenza='Scandiano', destinazione='Bologna', km=60,
                  andata_e_ritorno=True, tariffa_km=0.45, cliente='Rossi')
        # senza tariffa vale l'ultima usata
        r = self.tool('crea_rimborso_km', partenza='Scandiano', destinazione='Parma', km=50)
        self.assertFalse(r['isError'], r)
        self.assertEqual(r['structuredContent']['importo'], '22.50')
        r = self.tool('crea_rimborso_km', riepilogo_mensile=True, importo=310, data='2026-09-30')
        self.assertTrue(r['structuredContent']['riepilogo_mensile'])
        self.assertEqual(r['structuredContent']['importo'], '310.00')
        r = self.tool('crea_rimborso_km', riepilogo_mensile=True)
        self.assertIn('importo', r['structuredContent']['errori'])
        el = self.tool('elenco_rimborsi_km', stato='bozza')['structuredContent']
        self.assertEqual(el['conteggio'], 3)
        self.assertEqual(el['totali']['da_approvare'], '386.50')
        self.assertEqual(self.tool('elenco_rimborsi_km', cliente='Rossi')['structuredContent']
                         ['conteggio'], 1)
        # approvare: solo un amministratore, anche sui propri
        rid = el['elementi'][0]['id']
        r = self.tool('cambia_stato_rimborso_km', id=rid, stato='approvato')
        self.assertTrue(r['isError'])
        self.utente.profile.ruolo = Ruolo.AMMINISTRATORE
        self.utente.profile.save()
        r = self.tool('cambia_stato_rimborso_km', id=rid, stato='pagato')
        self.assertIn('approvato prima', r['structuredContent']['messaggio'])
        self.tool('cambia_stato_rimborso_km', id=rid, stato='approvato')
        r = self.tool('cambia_stato_rimborso_km', id=rid, stato='pagato', data_pagamento='2026-10-03')
        self.assertFalse(r['isError'], r)
        self.assertEqual(str(RimborsoChilometrico.objects.get(pk=rid).data_pagamento), '2026-10-03')

    def test_cliente(self):
        r = self.tool('crea_cliente', ragione_sociale='Verdi Srl', partita_iva='02643290353',
                      anteprima=True)
        self.assertTrue(r['structuredContent']['anteprima'])
        r = self.tool('crea_cliente', ragione_sociale='Verdi Srl', partita_iva='02643290353')
        self.assertFalse(r['isError'], r)
        r = self.tool('crea_cliente', ragione_sociale='Verdi Due', partita_iva='IT02643290353')
        self.assertIn('esiste già', r['structuredContent']['errori']['partita_iva'].lower())
