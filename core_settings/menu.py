"""Menu laterale e azioni della palette ⌘K, definiti una volta sola come dati.

La sidebar (`templates/components/_sidebar.html`) è un ciclo su quello che
restituisce `menu_per`: niente catene di `{% if active_nav == … %}` da
tenere allineate a mano. La palette legge le pagine dai link della sidebar,
quindi vede gli stessi nomi e gli stessi permessi.

La voce attiva si cerca prima per nome dell'URL della pagina (i due
scadenziari condividono la chiave `active_nav` 'scadenziario'), poi per la
chiave `active_nav` che la view mette nel contesto (pagine di dettaglio,
form, export che appartengono a una voce).
"""
from __future__ import annotations

from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class Voce:
    etichetta: str
    url_name: str
    icona: str
    attiva_con: tuple[str, ...] = ()
    # basta uno dei permessi; vuoto = tutti gli utenti autenticati
    permessi: tuple[str, ...] = ()
    # variabile di contesto che deve essere vera (es. modulo_magazzino_attivo)
    flag: str = ''
    # chiave di `nav_counts` (context processor sidebar_counts)
    contatore: str = ''


@dataclass(frozen=True)
class Gruppo:
    chiave: str          # data-group: chiave dello stato aperto/chiuso salvato
    titolo: str
    voci: tuple[Voce, ...]
    aperto: bool = False  # aperto finché l'utente non lo chiude
    breve: str = ''       # etichetta della barra in alto (vuota = titolo)


DOC = ('documenti.vedi',)
CASSA = ('cashflow.vedi',)
CRM = ('clienti.vedi',)

DASHBOARD = Voce('Dashboard', 'dashboard:home', 'dashboard', ('dashboard',))

GRUPPI: tuple[Gruppo, ...] = (
    Gruppo('vendite', 'Vendite', aperto=True, voci=(
        Voce('Fatture', 'documenti:fattura_list', 'doc', ('fatture',), DOC),
        Voce('Fatture ricorrenti', 'documenti:ricorrente_list', 'clock', ('fatture_ricorrenti',), DOC),
        Voce('Offerte', 'documenti:offerta_list', 'doc', ('offerte',), DOC),
        Voce('Ordini', 'documenti:ordine_list', 'doc', ('ordini',), DOC),
        Voce('DDT', 'documenti:ddt_list', 'doc', ('ddt',), DOC),
        Voce('Note di credito', 'documenti:nota_credito_list', 'doc', ('note_credito',), DOC),
    )),
    Gruppo('acquisti', 'Acquisti', voci=(
        Voce('Fatture di acquisto', 'documenti:fattura_acquisto_list', 'doc', ('fatture_acquisto',), DOC),
        Voce('DDT fornitore', 'documenti:ddt_fornitore_list', 'doc', ('ddt_fornitore',), DOC),
        Voce('Magazzino', 'magazzino:stock', 'grid', ('magazzino',), DOC,
             flag='modulo_magazzino_attivo'),
    )),
    Gruppo('incassi', 'Incassi e pagamenti', aperto=True, breve='Incassi', voci=(
        Voce('Scadenziario clienti', 'documenti:scadenziario_attivo', 'clock', ('scadenziario',), DOC),
        Voce('Scadenziario fornitori', 'documenti:scadenziario_passivo', 'clock',
             ('scadenziario_passivo',), DOC),
        Voce('Cashflow', 'cashflow:timeline', 'trendUp', ('cashflow',), CASSA),
        Voce('Scadenze fiscali', 'cashflow:scadenza_fiscale_list', 'briefcase', ('scadenze_fiscali',), CASSA),
        Voce('Liquidazione IVA', 'cashflow:liquidazione_iva', 'euro', ('liquidazione_iva',), CASSA),
        Voce('Spese ricorrenti', 'cashflow:spesa_ricorrente_list', 'clock', ('spese_ricorrenti',), CASSA),
        Voce('Rimborsi km', 'cashflow:rimborso_km_list', 'trendUp', ('rimborsi_km',), CASSA),
    )),
    Gruppo('crm', 'Clienti e CRM', breve='CRM', voci=(
        Voce('Clienti', 'clienti:list', 'users', ('clienti',), CRM, contatore='clienti'),
        Voce('Contatti', 'clienti:contatti_list', 'user', ('contatti',), CRM, contatore='contatti'),
        Voce('Opportunità', 'opportunita:list', 'target', ('opportunita',), CRM, contatore='opportunita'),
        Voce('Attività', 'attivita:list', 'calendar', ('attivita',), CRM),
        Voce('Team', 'team:index', 'team', ('team',)),
    )),
    Gruppo('analisi', 'Analisi', voci=(
        Voce('Dashboard documenti', 'documenti:dashboard', 'dashboard', ('dashboard_doc',), DOC),
        Voce('Vendite per cliente', 'documenti:vendite_per_cliente', 'chart', ('vendite',), DOC),
        Voce('Costi per categoria', 'documenti:costi_per_categoria', 'chart', ('costi',), DOC),
        Voce('Margine articoli', 'documenti:margine_articoli', 'trendUp', ('margine',), DOC),
        Voce('Top articoli', 'documenti:top_articoli', 'star', ('top_articoli',), DOC),
        Voce('Provvigioni agenti', 'documenti:provvigioni_agenti', 'briefcase', ('provvigioni',), DOC),
        Voce('Report', 'reports:index', 'chart', ('report',), ('report.vedi',)),
    )),
    Gruppo('anagrafiche', 'Anagrafiche', voci=(
        Voce('Fornitori', 'anagrafiche:fornitore_list', 'building', ('fornitori',), CRM),
        Voce('Articoli', 'anagrafiche:articolo_list', 'tag', ('articoli',), CRM),
        Voce('Posizioni IVA', 'anagrafiche:posizione_iva_list', 'briefcase', ('posizioni_iva',), CRM),
        Voce('Forme di pagamento', 'anagrafiche:forme_pagamento_list', 'euro', ('forme_pagamento',), CRM),
        Voce('Categorie di costo', 'anagrafiche:categoria_costo_list', 'tag', ('categorie_costo',), CRM),
        Voce('Anagrafica azienda', 'anagrafiche:anagrafica_azienda', 'building',
             ('anagrafica_azienda',), ('impostazioni.modifica',)),
    )),
)

IMPOSTAZIONI = Voce('Impostazioni', 'settings:utenti', 'settings', ('impostazioni',),
                    ('utenti.gestisci', 'impostazioni.modifica'))


def _visibile(voce: Voce, perms: dict, contesto) -> bool:
    if voce.permessi and not any(perms.get(p) for p in voce.permessi):
        return False
    return not voce.flag or bool(contesto.get(voce.flag))


def _url(voce: Voce) -> str:
    try:
        return reverse(voce.url_name)
    except NoReverseMatch:
        return ''


def menu_per(contesto) -> dict:
    """Il menu dell'utente della richiesta: solo voci che può aprire, con la
    voce attiva e i gruppi da tenere aperti. `contesto` è il contesto del
    template (serve `active_nav` della view, non solo la request)."""
    request = contesto.get('request')
    perms = contesto.get('user_perms_map') or {}
    conteggi = contesto.get('nav_counts') or {}
    active_nav = contesto.get('active_nav') or ''
    view_name = getattr(getattr(request, 'resolver_match', None), 'view_name', '') or ''

    def voce_dict(voce: Voce, gruppo: str) -> dict:
        return {
            'voce': voce, 'etichetta': voce.etichetta, 'icona': voce.icona,
            'url': _url(voce), 'gruppo': gruppo, 'attiva': False,
            'conteggio': conteggi.get(voce.contatore) if voce.contatore else None,
        }

    dashboard = voce_dict(DASHBOARD, '') if _visibile(DASHBOARD, perms, contesto) else None
    impostazioni = (voce_dict(IMPOSTAZIONI, 'Sistema')
                    if _visibile(IMPOSTAZIONI, perms, contesto) else None)
    gruppi = []
    for g in GRUPPI:
        voci = [voce_dict(v, g.titolo) for v in g.voci if _visibile(v, perms, contesto)]
        if voci:
            gruppi.append({'chiave': g.chiave, 'titolo': g.titolo, 'breve': g.breve or g.titolo,
                           'aperto': g.aperto, 'attivo': False, 'voci': voci})

    tutte = [v for v in (dashboard, impostazioni) if v]
    tutte += [v for g in gruppi for v in g['voci']]
    attiva = next((v for v in tutte if view_name and v['voce'].url_name == view_name), None)
    if attiva is None and active_nav:
        attiva = next((v for v in tutte if active_nav in v['voce'].attiva_con), None)
    if attiva is not None:
        attiva['attiva'] = True
        for g in gruppi:
            if attiva in g['voci']:
                g['aperto'] = g['attivo'] = True

    return {'dashboard': dashboard, 'gruppi': gruppi, 'impostazioni': impostazioni}


# Azioni della palette ⌘K: (titolo, gruppo, url_name, permesso)
AZIONI_PALETTE: tuple[tuple[str, str, str, str], ...] = (
    ('Nuova fattura', 'Vendite', 'documenti:fattura_create', 'documenti.modifica'),
    ('Nuova fattura ricorrente', 'Vendite', 'documenti:ricorrente_create', 'documenti.modifica'),
    ('Nuova offerta', 'Vendite', 'documenti:offerta_create', 'documenti.modifica'),
    ('Nuovo ordine', 'Vendite', 'documenti:ordine_create', 'documenti.modifica'),
    ('Nuovo DDT', 'Vendite', 'documenti:ddt_create', 'documenti.modifica'),
    ('Nuova nota di credito', 'Vendite', 'documenti:nota_credito_create', 'documenti.modifica'),
    ('Esporta per il commercialista', 'Vendite', 'documenti:export_commercialista', 'documenti.vedi'),
    ('Nuova fattura di acquisto', 'Acquisti', 'documenti:fattura_acquisto_create', 'documenti.modifica'),
    ('Importa fatture di acquisto (XML)', 'Acquisti', 'documenti:fattura_acquisto_import_xml',
     'documenti.modifica'),
    ('Nuova scadenza fiscale', 'Incassi e pagamenti', 'cashflow:scadenza_fiscale_create', 'cashflow.modifica'),
    ('Nuova spesa ricorrente', 'Incassi e pagamenti', 'cashflow:spesa_ricorrente_create', 'cashflow.modifica'),
    ('Nuovo rimborso chilometrico', 'Incassi e pagamenti', 'cashflow:rimborso_km_create', 'cashflow.modifica'),
    ('Nuovo cliente', 'Clienti e CRM', 'clienti:create', 'clienti.modifica'),
    ('Nuova opportunità', 'Clienti e CRM', 'opportunita:create', 'opportunita.modifica'),
    ('Nuova attività', 'Clienti e CRM', 'attivita:create', 'attivita.modifica'),
    ('Nuovo fornitore', 'Anagrafiche', 'anagrafiche:fornitore_create', 'clienti.modifica'),
    ('Nuovo articolo', 'Anagrafiche', 'anagrafiche:articolo_create', 'clienti.modifica'),
    ('Integrazioni PEC / SDI', 'Impostazioni', 'settings:integrazioni', 'impostazioni.modifica'),
    ('Intestazione documenti', 'Impostazioni', 'settings:documenti', 'impostazioni.modifica'),
)


def azioni_palette(perms: dict) -> list[dict]:
    return [
        {'titolo': titolo, 'sotto': gruppo, 'url': reverse(url_name)}
        for titolo, gruppo, url_name, permesso in AZIONI_PALETTE
        if perms.get(permesso)
    ]
