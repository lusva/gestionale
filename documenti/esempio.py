"""Documenti di esempio per l'anteprima in Impostazioni › Documenti.

Fattura, offerta e DDT con dati inventati e oggetti mai salvati, resi con gli
stessi template e lo stesso calcolo dei totali dei documenti veri: così
l'amministratore vede logo, intestazione, piè di pagina e colore come
usciranno, anche prima di aver emesso documenti. Del gestionale si usano solo
l'anagrafica dell'azienda e il suo conto corrente predefinito.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.utils import timezone

TIPI = {
    'fattura': 'Fattura',
    'offerta': 'Offerta',
    'ddt': 'Documento di trasporto',
}


def _cliente():
    from clienti.models import Cliente
    return Cliente(
        ragione_sociale='Rossi Arredamenti S.r.l.',
        indirizzo='Via Emilia Est 214', cap='41121', citta='Modena', provincia='MO',
        partita_iva='IT02345670361', codice_fiscale='02345670361',
        codice_sdi='M5UXCR1', pec='amministrazione@pec.rossiarredamenti.it',
    )


def _righe():
    from anagrafiche.models import PosizioneIva
    from documenti.models import RigaDocumento
    iva = PosizioneIva(descrizione='IVA 22%', aliquota=Decimal('22'))
    voci = [
        ('Consulenza progettuale\nAnalisi dei requisiti e disegno della soluzione', '12', '85.00', 'ORE'),
        ('Sviluppo modulo gestionale', '1', '2400.00', None),
        ('Canone di assistenza annuale\nAggiornamenti, backup e supporto via email', '1', '480.00', None),
    ]
    return [
        RigaDocumento(numero_riga=n, descrizione_libera=testo, quantita=Decimal(q),
                      importo_unitario=Decimal(p), um=um, iva=iva)
        for n, (testo, q, p, um) in enumerate(voci, start=1)
    ]


def _forma_pagamento():
    from anagrafiche.models import FormePagamento
    return FormePagamento(tipo_pagamento='Bonifico bancario a 30 giorni data fattura')


def _conto(azienda):
    """Il conto vero dell'azienda, se c'è: in anteprima si controlla anche quello."""
    from anagrafiche.models import ContoCorrente
    from documenti.utils import resolve_conto_corrente
    if azienda is not None and azienda.pk:
        conto = resolve_conto_corrente(SimpleNamespace(), azienda)
        if conto is not None:
            return conto
    return ContoCorrente(banca='Banca di esempio', intestatario=getattr(azienda, 'ragione_sociale', ''),
                         iban='IT60X0542811101000000123456', bic_swift='BPMOIT22XXX')


def contesto(tipo: str, azienda):
    """(template, contesto) del documento di esempio ``tipo`` (chiave di TIPI)."""
    from documenti.models import TestataDdt, TestataFattura, TestataOfferta
    from documenti.utils import calcola_valori

    oggi = timezone.localdate()
    righe = _righe()
    if tipo == 'fattura':
        fattura = TestataFattura(
            numero=128, anno=oggi.year, data_documento=oggi, tipo_documento='TD01',
            cliente=_cliente(), forme_pagamento=_forma_pagamento(),
            sdi_stato=TestataFattura.SdiStato.INVIATA,
            note='Documento di esempio generato per l\'anteprima.',
        )
        totals = calcola_valori(fattura, righe=righe, azienda=azienda)
        return 'documenti/pdf/fattura.html', {
            'fattura': fattura, 'azienda': azienda, 'righe': righe, 'totals': totals,
            'scadenze': [SimpleNamespace(data=oggi + timedelta(days=30), importo=totals['totale'])],
            'conto_corrente': _conto(azienda),
        }
    if tipo == 'offerta':
        documento = TestataOfferta(
            numero=42, anno=oggi.year, data_documento=oggi, cliente=_cliente(),
            scadenza=oggi + timedelta(days=30), forme_pagamento=_forma_pagamento(),
            note='Documento di esempio generato per l\'anteprima.',
        )
        return 'documenti/pdf/documento.html', {
            'documento': documento, 'azienda': azienda, 'righe': righe,
            'doc_label': 'Offerta', 'doc_kind': 'offerta', 'counterparty': 'cliente',
            'has_stato': True, 'show_iva': True, 'show_totals': True,
            'totals': calcola_valori(documento, righe=righe, azienda=azienda),
        }
    if tipo == 'ddt':
        documento = TestataDdt(
            numero=17, anno=oggi.year, data_documento=oggi, cliente=_cliente(),
            causale_trasporto='Vendita', porto='Franco', aspetto_beni='Scatole',
            vettore='Mittente', cura='Mittente', numero_palette=3,
            peso_netto=Decimal('42.5'), peso_lordo=Decimal('46'),
            data_ora_ritiro=timezone.now(),
        )
        return 'documenti/pdf/documento.html', {
            'documento': documento, 'azienda': azienda, 'righe': righe,
            'doc_label': 'DDT', 'doc_kind': 'ddt', 'counterparty': 'cliente',
            'has_stato': True, 'show_iva': False, 'show_totals': False, 'totals': None,
        }
    raise KeyError(tipo)
