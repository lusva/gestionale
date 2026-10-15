# Piano di sviluppo — 6 interventi

Brief di implementazione per Claude Code. Sei interventi sul gestionale
(Django 5.2 + HTMX + Alpine, SQLite), da fare **uno alla volta, nell'ordine
indicato**, ognuno completo di migrazioni, interfaccia, test e verifica prima
di passare al successivo.

| # | Intervento | Perché | Dipende da |
|---|---|---|---|
| 1 | [Riorganizzazione della sidebar](#1-riorganizzazione-della-sidebar) | 8 gruppi chiusi: quasi tutto richiede due clic | — |
| 2 | [Pagina Integrazioni (PEC / SDI)](#2-pagina-integrazioni-pec--sdi) | oggi la PEC si configura solo dall'admin di Django | — |
| 3 | [Fatture di acquisto inserite a mano](#3-fatture-di-acquisto-inserite-a-mano) | fornitori esteri e fatture cartacee non si possono registrare | — |
| 4 | [Fatture ricorrenti](#4-fatture-ricorrenti) | canoni mensili oggi si rifanno a mano con «Duplica» | — |
| 5 | [Incassi parziali](#5-incassi-parziali) | una fattura oggi è solo «incassata sì/no» | — |
| 6 | [Note di credito al SDI](#6-note-di-credito-al-sdi) | le note di credito non generano XML né si inviano | 5 (per lo storno sui crediti) |

---

## Regole di lavoro (valgono per tutti gli interventi)

**Ambiente**
- Usa sempre il virtualenv del progetto: `venv/bin/python manage.py …`.
- Il working tree contiene modifiche non committate dell'utente (anche in
  `api/mcp_tools.py`, `cashflow/iva.py`, `dashboard/views.py`…): non
  ripristinarle, non sovrascriverle, lavoraci sopra. **Non fare commit** se
  l'utente non lo chiede.
- Il database `db.sqlite3` contiene dati reali: per le prove nel browser che
  scrivono dati usa una copia (vedi [Verifica](#verifica-di-ogni-intervento)).
- Istruzioni globali dell'utente: dopo aver eseguito i test, rimuovi i file
  di test che hai creato.

**Stile del codice** (adeguati a quello che c'è)
- Nomi, commenti, docstring e testi dell'interfaccia in italiano. I commenti
  spiegano il *perché*, non il cosa. Guarda `documenti/servizi.py`,
  `documenti/anteprima.py`, `dashboard/da_fare.py` come riferimento.
- La logica di dominio va in funzioni di servizio (stile `documenti/servizi.py`),
  non nelle view: le usano anche API v2 (`api/v2.py`) e server MCP
  (`api/mcp_tools.py`, usato dall'assistente NIKI).
- Importi `Decimal` arrotondati al centesimo (`quantize(Decimal('0.01'))`),
  date con `localdate()`.
- Permessi: chiavi in `accounts/permissions.py` (`PERM_MATRIX`), decoratore
  `require_perm('…')` o `PermRequiredMixin`; nei template
  `user_perms_map|get_item:'…'`. Eventi rilevanti nel log di audit con
  `audit.utils.log(Azione…, target=…, request=…, meta=…)` — mai password o
  segreti nei `meta`.

**Architettura**
- SQLite in WAL con `transaction_mode: IMMEDIATE`: una transazione che
  scrive prende subito il lock. Nelle view GET non aprire transazioni se non
  devi scrivere (vedi `cashflow/iva.py: sincronizza` e
  `SpesaRicorrente.genera_scadenze`).
- Niente Celery: i lavori periodici sono comandi di gestione + timer systemd
  in `deploy/` (modello: `gestionale-sdi-pull.*`, `gestionale-backup.*`).
- Evita le N+1: aggregati nel database (`aggregate`, `TruncMonth`,
  sottoquery come `documenti.models.scadenze_acquisto_con_residuo`),
  `select_related`/`prefetch_related`. Per i totali dei documenti usa
  `documenti.utils.calcola_valori(testata, righe, azienda=azienda_per_calcoli())`
  (gli stessi conti di PDF e FatturaPA).

**Componenti dell'interfaccia da riusare** (non reinventarli)
- Righe dei documenti: `templates/documenti/_righe_formset.html` +
  `data-formset` in `static/js/app.js` (aggiungi/togli righe, Invio = nuova
  riga, importo di riga, numerazione). Il JS è generico sul prefisso del
  formset: funziona anche per formset con campi diversi.
- Riepilogo live dei totali: `documenti/anteprima.py` (registro
  `tipo → modello, form, formset`) + `templates/documenti/_riepilogo_documento.html`
  con `hx-post` e `hx-trigger="load, riepilogo from:body"`.
- Select con ricerca: attributo `data-cerca` (o select con ≥ 15 voci);
  precompilati da `<option data-…>`: widget `documenti.forms.SelectConDati`.
- Layout form con colonna di riepilogo: classe `layout-documento`;
  finestre: `<dialog class="dialogo">` + `data-apri-dialogo="id"`;
  paginazione: `{% querystring page=n %}`; tabelle larghe: `.tbl-scroll`.
- «Da fare» (campanella e dashboard): aggiungi voci in
  `dashboard/da_fare.py: voci_da_fare` (solo aggregati, nessuna query per riga).
- Palette comandi (⌘K): le pagine le legge dalla sidebar, le azioni da
  `templates/components/_palette.html` (JSON con permessi).
- I link a download (PDF, XML, ZIP, export) devono avere `pdf`, `xml`, `zip`
  o `export` nell'URL oppure l'attributo `download`: così sono esclusi dal
  prefetch (`<script type="speculationrules">` in `templates/base.html`).
- I form POST sono protetti dal doppio clic in `static/js/app.js`; ⌘S salva i
  form con `data-salva-rapido`; `data-avvisa-modifiche` avvisa se si esce
  senza salvare.

---

## 1. Riorganizzazione della sidebar

**Stato attuale**
- `templates/components/_sidebar.html`: 8 gruppi `<details>` (Principale,
  Documenti con una sola voce, Ciclo attivo, Ciclo passivo, Cashflow,
  Analisi, Anagrafiche, Sistema con una sola voce). Il gruppo aperto si
  decide con lunghe catene `{% if active_nav == … or … %}`, fragili.
- Lo stato aperto/chiuso è in `localStorage` (`sidebar.group.<data-group>`)
  e si applica prima del disegno con lo script in linea subito dopo la
  sidebar in `templates/base.html`; il gruppo della pagina attiva resta
  sempre aperto. Su mobile la sidebar è un cassetto (`.sidebar.open` +
  `.sfondo-menu`, `toggleSidebar()` in `app.js`).
- Tutte le voci si vedono anche senza permesso (il clic porta a un errore).
- La palette (⌘K) costruisce l'elenco delle pagine dai link della sidebar
  (testo dello `<span>` e titolo del gruppo).

**Cosa fare**
1. Definisci il menu una volta sola come dati Python, es.
   `core_settings/menu.py`: gruppi con chiave, titolo, voci (etichetta,
   `url_name`, icona, chiavi `active_nav` che la attivano, permesso,
   eventuale flag come `modulo_magazzino_attivo`, eventuale contatore). Un
   context processor o un inclusion tag lo filtra per permessi e calcola
   gruppo e voce attivi; il template diventa un ciclo. Non cambiare le
   chiavi `active_nav` già usate dalle view.
2. Nuova struttura (etichette brevi, in ordine di uso):
   - in alto, fuori dai gruppi: **Dashboard**
   - **Vendite**: Fatture, Fatture ricorrenti (dopo l'intervento 4), Offerte,
     Ordini, DDT, Note di credito
   - **Acquisti**: Fatture di acquisto, DDT fornitore, Magazzino (solo se il
     modulo è attivo)
   - **Incassi e pagamenti**: Scadenziario clienti (oggi «Scadenziario
     attivo»), Scadenziario fornitori, Cashflow, Scadenze fiscali,
     Liquidazione IVA, Spese ricorrenti, Rimborsi km
   - **Clienti e CRM**: Clienti, Contatti, Opportunità, Attività
   - **Analisi**: Dashboard documenti, Vendite per cliente, Costi per
     categoria, Margine articoli, Top articoli, Provvigioni agenti, Report
   - **Anagrafiche**: Fornitori, Articoli, Posizioni IVA, Forme di pagamento,
     Categorie di costo, Anagrafica azienda
   - in fondo, accanto all'utente: **Impostazioni** (link singolo, non un
     gruppo)
3. Aperti di default: Vendite, Incassi e pagamenti e il gruppo della pagina
   attiva. Gli altri ricordano lo stato scelto dall'utente: usa un nuovo
   prefisso di chiave (`sidebar.v2.`) così gli stati vecchi non contano.
4. Nascondi gruppi e voci che l'utente non può aprire
   (`documenti.vedi`, `cashflow.vedi`, `report.vedi`, `clienti.vedi`,
   `utenti.gestisci`/`impostazioni.modifica` per le Impostazioni).
5. Mantieni i contatori (clienti, contatti, opportunità aperte) e il
   comportamento su mobile.

**Accettazione**
- [ ] Ogni pagina evidenzia la sua voce; il suo gruppo è aperto al
      caricamento, senza sfarfallio.
- [ ] Un utente «Visualizzatore» non vede voci che non può aprire.
- [ ] La palette ⌘K elenca tutte le pagine con i nuovi nomi dei gruppi.
- [ ] Tutti i link del menu rispondono 200 (smoke test su tutte le voci, per
      ogni ruolo).
- [ ] Nessuna catena `{% if active_nav == … %}` rimasta nel template.

---

## 2. Pagina Integrazioni (PEC / SDI)

**Stato attuale**
- `/impostazioni/integrazioni/` è un segnaposto (`core_settings/views.py:
  placeholder`, `core_settings/urls.py`).
- I campi stanno su `anagrafiche.models.AnagraficaAzienda`: `sdi_provider`
  (disabilitato / mock / pec), `pec_mittente`, `pec_destinatario_sdi`,
  `pec_smtp_host/port/user/password/use_tls`,
  `pec_imap_host/port/user/password/folder`. Oggi si modificano solo da
  `/admin/` (`AnagraficaAziendaForm` in `anagrafiche/forms.py` non li ha).
- Verifica della configurazione: comando
  `documenti/management/commands/sdi_verifica.py` (login SMTP e IMAP,
  cartella, email da elaborare; non invia nulla). Backend in
  `documenti/sdi.py` (`PECBackend._smtp_connect`, `_imap_configurato`,
  `_imap_connect`, `cerca_recenti`). Ricezione: comando `sdi_pull` ogni
  15 minuti (timer systemd); email elaborate in `MessaggioPecElaborato`,
  notifiche in `MessaggioSdi`.
- Il pulsante «Invia al SDI» compare solo se `sdi_provider` non è
  «disabilitato» (`FatturaDetailView`, contesto `sdi_attivo`). Se le fatture
  si emettono da un altro canale (es. il portale Aruba) il provider deve
  restare disabilitato, altrimenti si rischiano doppie emissioni.

**Cosa fare**
1. Pagina `/impostazioni/integrazioni/` (permesso `impostazioni.modifica`)
   con un form sui soli campi SDI/PEC.
   - Password con `PasswordInput(render_value=False)`: vuote = si tengono
     quelle salvate; una casella «cancella la password» per svuotarle.
     Mai rimandare la password nell'HTML.
   - Spiegazione chiara di `sdi_provider` («Disabilitato» se emetti da un
     altro canale) e di `pec_destinatario_sdi` (il primo invio va a
     `sdi01@pec.fatturapa.it`, poi `sdi_pull` salva la casella da cui il SDI
     risponde).
   - Preimpostazione «Aruba PEC» (dati già nel README: `smtps.pec.aruba.it`
     465, `imaps.pec.aruba.it` 993) che riempie host e porte. Per gli altri
     gestori lascia i campi liberi: non inventare host.
2. Pulsante **«Verifica connessione»** (POST, nessun invio): sposta i
   controlli di `sdi_verifica` in una funzione riusabile, es.
   `documenti.sdi.verifica_configurazione(azienda) -> list[Esito]`
   (esito ok/ko + testo), usata sia dal comando sia dalla pagina. Timeout già
   presenti in `sdi.py`; la richiesta deve restare ben sotto i 120 s di
   gunicorn.
3. Riquadro di stato: provider attivo o no, casella SDI in uso, ultima email
   elaborata (`MessaggioPecElaborato`), ultimi 10 `MessaggioSdi`, fatture in
   stato «scartata»/«rifiutata». Se il provider è PEC e l'ultima elaborazione
   è più vecchia di un'ora, avvisa che il timer `sdi_pull` forse non gira.
4. Facoltativo: pulsante «Controlla ora la PEC» che esegue `sdi_pull` con
   `--limit` basso (es. 30) e ne mostra il riepilogo.
5. Log di audit sulle modifiche (nomi dei campi cambiati, mai i valori delle
   password).

**Accettazione**
- [ ] Si configura la PEC senza passare dall'admin; lasciando vuota la
      password resta quella salvata.
- [ ] «Verifica connessione» mostra OK/KO per anagrafica, SMTP e IMAP; con
      credenziali errate mostra l'errore e non va in 500.
- [ ] Un utente senza `impostazioni.modifica` non vede né modifica le
      credenziali.
- [ ] Test: password mantenuta se vuota, permessi, verifica con `smtplib` e
      `imaplib` simulati (mock), nessun invio reale.

---

## 3. Fatture di acquisto inserite a mano

**Stato attuale**
- `documenti.models`: `TestataFatturaAcquisto` (eredita da
  `TestataDocumento`: numero interno auto-progressivo per anno,
  `forme_pagamento`, `note`…; in più `numero_fornitore`, `data_fornitore`,
  `tipo_documento`, `stato` bozza/confermata/annullata, `imposta`,
  `totale_documento`, `pagata`, `xml_originale`), `RigaFatturaAcquisto`
  (descrizione, quantità, prezzo, sconto %, `aliquota_iva`,
  `categoria_costo`), `ScadenzaFatturaAcquisto`, `PagamentoScadenzaAcquisto`.
- Si creano solo dall'import XML (`documenti/xml_import.py`, `sdi_pull`) o
  dall'admin. Esistono lista, dettaglio, conferma, «segna pagata»,
  registrazione pagamenti (`documenti/views.py`, template
  `fattura_acquisto_list.html` / `fattura_acquisto_detail.html`).
- Totali: il signal `_recalc_fattura_acquisto` li ricalcola dalle righe; per
  le fatture con `xml_originale` tiene il totale dell'XML.
- Vincolo di unicità `(fornitore, numero_fornitore, data_fornitore)`.
- `categoria_costo` si imposta solo dall'admin, ma alimenta «Costi per
  categoria».
- `Fornitore` (`AbstractAnagrafica`) non ha una forma di pagamento
  predefinita.
- Il magazzino non si muove con le fatture di acquisto (solo con le righe
  `RigaDocumento`, es. DDT fornitore): resta così.

**Cosa fare**
1. View crea / modifica / elimina: `/documenti/fatture-acquisto/nuova/`,
   `<pk>/modifica/`, `<pk>/elimina/` (`documenti.modifica`,
   `documenti.elimina`). Pulsante «Nuova fattura di acquisto» in lista,
   azione nella palette.
2. Form testata: fornitore (`data-cerca`), numero e data del fornitore
   (obbligatori), data di registrazione (oggi), tipo documento (TD01; TD04
   per le note di credito dei fornitori), stato (default «confermata» per
   quelle inserite a mano), forma di pagamento, note, **allegato** (PDF o
   scansione).
   - Nuovo campo `allegato = FileField(upload_to='fatture_acquisto_allegati/')`.
     Aggiungi la cartella a `PROTETTI` in `gestionale/media_views.py`
     (permesso `documenti.vedi`) e alla regex della location in
     `deploy/nginx-gestionale.conf`.
3. Righe: formset di `RigaFatturaAcquisto` con le stesse convenzioni
   `data-formset` (nuovo partial, es. `_righe_acquisto_formset.html`).
   Riepilogo live: sposta i conti del signal `_recalc_fattura_acquisto` in
   una funzione pura (es. `totali_acquisto(righe)`) usata dal signal e
   dall'anteprima; registra il tipo in `documenti/anteprima.py` o aggiungi
   un endpoint dedicato.
4. Scadenze: formset di `ScadenzaFatturaAcquisto` (data, importo, modalità,
   IBAN). Se l'utente non le tocca, generale dalla forma di pagamento al
   salvataggio, come per le fatture di vendita
   (`utils.genera_scadenze_da_forma_pagamento`, logica di
   `servizi.sincronizza_scadenze`). Non rigenerarle mai se ci sono pagamenti
   registrati.
5. Facoltativo ma utile: campo `forme_pagamento` su `Fornitore` (proposto
   nelle fatture nuove, con `SelectConDati` come per i clienti) e nella sua
   scheda.
6. Fatture importate da XML: «Modifica» limitata a categoria di costo delle
   righe, stato, data di registrazione, note e allegato. Importi e righe
   restano in sola lettura (fa fede l'XML).
7. Doppione `(fornitore, numero_fornitore, data_fornitore)`: errore chiaro
   nel form con link alla fattura esistente.
8. Eliminazione: solo per quelle inserite a mano e senza pagamenti
   registrati; altrimenti messaggio che spiega perché.

**Accettazione**
- [ ] Fattura a mano con una riga al 22% e una esente: imponibile, imposta e
      totale corretti; scadenze generate dalla forma di pagamento.
- [ ] Compare in scadenziario fornitori, cashflow, liquidazione IVA (se
      confermata, per data di registrazione) e «Costi per categoria».
- [ ] Numero fornitore già registrato: rifiutato con messaggio.
- [ ] Su una fattura importata si cambia la categoria di costo ma non gli
      importi.
- [ ] L'allegato si scarica solo da utenti con `documenti.vedi`.

---

## 4. Fatture ricorrenti

**Stato attuale**
- Nessun modello. Esiste «Duplica» (`?da=<pk>` su
  `/documenti/fatture/nuova/`, `documenti/anteprima.iniziali_da_copia`).
- Le **spese** ricorrenti esistono già (`cashflow.models.SpesaRicorrente`:
  `Periodicita`, `giorno_del_mese`, `date_attese()`, `_giorno_in_mese()`,
  generazione idempotente): stesso schema da riusare per la periodicità.
- Creazione fattura con numerazione, righe e scadenze:
  `documenti.servizi.crea_documento('fattura', dati)` (lo usano API e MCP).

**Cosa fare**
1. Modelli in `documenti`:
   - `FatturaRicorrente`: nome (es. «Canone assistenza JANUS»), cliente,
     attiva, periodicità (riusa `cashflow.models.Periodicita`), giorno di
     emissione (1–31: 31 = ultimo giorno del mese, come `_giorno_in_mese`),
     data inizio, data fine (facoltativa), tipo documento (TD01), sezionale,
     forma di pagamento, conto corrente, note in fattura,
     `competenza` («periodo corrente» / «periodo precedente»), invio
     automatico al SDI (default no) e per email (default no).
   - `RigaFatturaRicorrente`: ordine, articolo (facoltativo), descrizione,
     quantità, UM, prezzo, IVA.
   - `EmissioneRicorrente`: ricorrenza, periodo (data prevista), fattura
     creata (`SET_NULL`), data, esito/errore. **UniqueConstraint
     (ricorrenza, periodo)**: è la garanzia di non emettere mai due volte lo
     stesso periodo.
2. Segnaposto nelle descrizioni e nelle note: `{mese}` (nome italiano),
   `{anno}`, `{periodo}` («ottobre 2026», «1° trimestre 2026»…),
   `{dal}` / `{al}` (date del periodo di competenza).
3. Servizio `documenti/ricorrenti.py`:
   - `da_emettere(ricorrenza, oggi)`: periodi previsti da data inizio a oggi
     non ancora emessi (rispettando data fine e sospensione);
   - `emetti(ricorrenza, periodo)`: costruisce i `dati` e chiama
     `servizi.crea_documento('fattura', dati)`, in una transazione per
     emissione; registra `EmissioneRicorrente`; un errore resta registrato
     e non blocca le altre.
   - **Data della fattura = giorno dell'emissione, mai nel passato**: se si
     recuperano periodi saltati, le fatture hanno la data di oggi e il
     periodo di competenza nella descrizione. Così numerazione e date restano
     progressive nell'anno (una fattura con numero successivo non può avere
     una data precedente).
4. Invio automatico (se attivato sulla ricorrenza): prima sposta la logica di
   `documenti/views.fattura_invia_sdi` (lucchetto contro i doppi invii,
   tentativo, `MessaggioSdi`) e di `fattura_invia_email` in funzioni di
   servizio riusate da view, comando e MCP; poi chiamale dopo l'emissione.
   Il SDI solo se `sdi_provider` è attivo.
5. Comando `genera_fatture_ricorrenti` (`--dry-run`, `--data AAAA-MM-GG`)
   + timer `deploy/gestionale-ricorrenti.{service,timer}` (ogni giorno alle
   6:00) + istruzioni nel README.
6. Interfaccia (Vendite › Fatture ricorrenti):
   - lista: nome, cliente, importo per periodo, prossima emissione, stato;
   - form con righe (convenzioni `data-formset`) e riepilogo live
     (`calcola_valori` su una `TestataFattura` e `RigaDocumento` non salvate,
     come fa `documenti/anteprima.py`);
   - dettaglio con lo storico delle emissioni e i link alle fatture;
     pulsanti «Emetti ora i periodi scaduti», «Sospendi» / «Riattiva»;
   - nel dettaglio fattura, menu «Altro»: «Crea una ricorrenza da questa
     fattura».
7. «Da fare»: fatture ricorrenti emesse oggi da controllare/inviare
   (info), emissioni non riuscite (urgente), prossime emissioni entro 7
   giorni (info).
8. Facoltativo: le emissioni future nella finestra del cashflow come incassi
   attesi.

**Accettazione**
- [ ] Ricorrenza mensile il 31: a febbraio emette il 28 (o 29).
- [ ] Eseguire il comando due volte nello stesso giorno non crea doppioni.
- [ ] Due mesi saltati: due fatture datate oggi, con i due periodi nelle
      descrizioni, numerate in sequenza.
- [ ] Data fine e sospensione rispettate; scadenze generate dalla forma di
      pagamento.
- [ ] Un errore (es. cliente senza IVA predefinita e riga senza IVA) è
      registrato e visibile, le altre ricorrenze proseguono.

---

## 5. Incassi parziali

**Stato attuale**
- `TestataFattura` ha solo `pagata` e `data_pagamento`; `ScadenzaFattura`
  ha data e importo, nessun incasso.
- Segnare pagata: `documenti.servizi.segna_pagata(fattura, data)`, chiamata
  dalla view `fattura_toggle_pagata` (popover della lista
  `_fattura_pagamento.html` e dettaglio) e dal tool MCP `_segna_pagata`.
- Il ciclo passivo ha già tutto lo schema da copiare:
  `PagamentoScadenzaAcquisto`, proprietà `importo_pagato` /
  `importo_residuo` / `stato` di `ScadenzaFatturaAcquisto`, sottoquery
  `scadenze_acquisto_con_residuo`, signal
  `_recalc_stato_pagamento_fattura_acquisto`, view
  `pagamento_scadenza_acquisto_create`, form nel template
  `fattura_acquisto_detail.html`, `servizi.segna_pagata_acquisto` /
  `annulla_pagamento_acquisto`.

**Cosa fare**
1. Modello `IncassoScadenza` (`scadenza` → `ScadenzaFattura`,
   `related_name='incassi'`; data, importo > 0, modalità come
   `ScadenzaFatturaAcquisto.ModalitaPagamento`, note). Su `ScadenzaFattura`:
   proprietà `importo_incassato`, `importo_residuo`, `stato`
   (aperta / parziale / incassata) e la sottoquery gemella
   `scadenze_fattura_con_residuo(qs)` (`incassato_db`, `residuo_db`).
2. Signal sugli incassi (salvataggio ed eliminazione): ricalcola
   `TestataFattura.pagata` (tutte le scadenze a residuo zero) e
   `data_pagamento` (ultimo incasso) con `.update()`. Le fatture senza
   scadenze mantengono il flag manuale.
3. Servizi: `registra_incasso(scadenza, importo, data, modalita)` (importo
   > 0 e ≤ residuo), `segna_pagata` → registra un incasso per ogni residuo
   aperto (come `segna_pagata_acquisto`), annullamento → elimina gli
   incassi.
4. **Migrazione dati**: per ogni fattura già `pagata=True` con scadenze, un
   incasso per scadenza dell'intero importo alla `data_pagamento` (o alla
   data della scadenza). Reversibile (gli incassi creati dalla migrazione
   devono essere riconoscibili, es. dalle note).
5. **Attenzione**: `servizi.sincronizza_scadenze` cancella e ricrea le
   scadenze (con CASCADE cancellerebbe gli incassi). Non deve mai toccare le
   scadenze di una fattura con incassi. Nel form fattura le scadenze con
   incassi sono in sola lettura (o la validazione ne impedisce la modifica).
6. Usa il residuo, non l'importo pieno, ovunque si calcolano crediti:
   - `documenti/analytics.py` (scadenziario attivo) e il suo template;
   - `documenti/export_csv.py` (export scadenziario);
   - `cashflow/views.py` (incassi attesi);
   - `dashboard/views.py` (crediti scaduti);
   - `dashboard/da_fare.py`;
   - `documenti/dashboard.py` (aging);
   - `documenti/estratto_conto.py`;
   - `documenti/views.py` (dettaglio: `scaduta`, importo scaduto, testi del
     sollecito; lista: filtro «Scadute» e colonna scadenza);
   - `documenti/management/commands/invia_promemoria_scadenze.py`;
   - `documenti/commercialista.py` (registro vendite);
   - `api/mcp_tools.py` (`_scheda_cliente`, `_scadenziario_attivo`,
     `_segna_pagata`) e `api/serializers.py`: il file MCP ha modifiche non
     committate dell'utente, intervieni in modo mirato.
7. Interfaccia:
   - dettaglio fattura: tabella scadenze con incassato / residuo / stato,
     «Registra incasso» per scadenza (come nel dettaglio acquisti), elenco
     degli incassi con «Elimina»;
   - lista: badge «Parziale» con importo residuo;
   - il popover «Segna incassata» resta il saldo totale.
8. Facoltativo: tool MCP `registra_incasso` speculare a
   `registra_pagamento_fornitore`.

**Accettazione**
- [ ] Fattura da 1.000 € con due rate da 500: incasso di 300 sulla prima →
      stato «parziale», residuo 700; scadenziario, cashflow, «Da fare» ed
      estratto conto mostrano 200 + 500.
- [ ] Incassato il resto → `pagata` vero, data = ultimo incasso; eliminato un
      incasso → di nuovo da incassare.
- [ ] Cambiare forma di pagamento o righe di una fattura con incassi non
      cancella gli incassi.
- [ ] Dopo la migrazione, le fatture già pagate hanno residuo zero ovunque.

---

## 6. Note di credito al SDI

**Stato attuale**
- `TestataNotaCredito` (TD04/TD08, sezionale proprio, collegata alle fatture
  con `TestataFattura.nota_credito_collegata` / `fatture_origine`) non ha
  campi SDI. Si crea da una fattura (`servizi.converti('fattura_in_nota_credito')`,
  copia le righe, una nota per fattura) o a mano (view generiche
  `documenti/doc_generic.py`, dettaglio `templates/documenti/doc_detail.html`).
- XML: `documenti/fatt_el.build_fattura_xml(fattura, tipo_documento=None)`
  lavora su `TestataFattura` (usa `scadenze`, `sezionale`, `tipo_documento`,
  `numero_documento_sdi`).
- Progressivo del file: `fatt_el.progressivo_sdi` = `pk * 36 + tentativo` in
  base 36, 5 caratteri. `sdi_pull.match_fattura` fa il contrario e
  riconduce le notifiche solo alle fatture.
- Invio: `documenti/views.fattura_invia_sdi` (lucchetto contro i doppi invii
  su `sdi_ultimo_messaggio`, tentativo per i reinvii dopo scarto,
  `MessaggioSdi`); `PECBackend.send_xml` scrive «Fattura n/anno» nell'oggetto.
- `MessaggioSdi.fattura` è una FK solo verso `TestataFattura`.
- La liquidazione IVA sottrae già le note di credito (`cashflow/iva.py`).
- Una fattura stornata da una nota di credito resta tra i crediti da
  incassare (solleciti compresi).

**Cosa fare**
1. Campi SDI su `TestataNotaCredito`, gli stessi di `TestataFattura`
   (`sdi_stato` con le stesse scelte, `sdi_data_invio`,
   `sdi_id_trasmissione`, `sdi_ultimo_messaggio`, `tentativo_sdi`,
   proprietà `trasmessa_sdi` / `emessa_sdi`). Puoi estrarre un mixin
   astratto, ma verifica con `makemigrations` che su `TestataFattura` non
   compaia nessuna `AlterField`.
2. `MessaggioSdi`: FK facoltativa `nota_credito` (e `fattura` facoltativa)
   con vincolo «esattamente una»; adegua admin e template.
3. **Progressivo senza collisioni**: il SDI rifiuta un nome file già
   ricevuto dallo stesso trasmittente. Le fatture tengono lo schema attuale
   (prima cifra numerica fino a pk 466.559: aggiungi un controllo che lo
   garantisca); le note di credito usano `N` + 4 caratteri base 36 di
   `pk * 36 + tentativo` (fino a pk 46.655). Documenta la regola in
   `progressivo_sdi`.
4. XML TD04/TD08: generalizza il builder (es. `build_documento_xml(doc)`):
   - importi **positivi** (convenzione FatturaPA per le note di credito);
   - `DatiFattureCollegate` con `IdDocumento` (= `numero_documento_sdi`
     della fattura) e `Data` per ogni fattura in `fatture_origine`;
   - niente `DatiPagamento` salvo scadenze esplicite;
   - sconti, spese, cassa e bollo come per le fatture (`calcola_valori`).
   Prima di abilitarlo, valida un XML di esempio con lo schema XSD ufficiale
   FatturaPA (es. un test che usa `lxml.etree.XMLSchema` se il file XSD è
   presente in locale; non scaricarlo durante i test).
5. Invio: sposta la logica di `fattura_invia_sdi` in un servizio unico per
   fatture e note (es. `servizi.invia_al_sdi(documento)`); oggetto e testo
   della PEC per tipo di documento. View «Invia al SDI» / «Reinvia» anche
   per le note di credito, solo se il provider è attivo.
6. `sdi_pull`: `match_documento(m)` → fattura o nota di credito (per
   `IdentificativoSdI` su entrambe, per progressivo con prefisso `N` sulle
   note); stato e `MessaggioSdi` sul documento giusto.
7. Interfaccia della nota di credito: «XML FatturaPA», «Invia al SDI»,
   riquadro stato SDI e messaggi (come `fattura_detail.html`), filtro per
   stato SDI in lista, modifica ed eliminazione bloccate dopo l'emissione
   (stesse regole delle fatture), voci in «Da fare» per scartate e da
   inviare.
8. Storno sui crediti (dopo l'intervento 5): una nota di credito collegata a
   una fattura registra un incasso di compensazione (modalità
   «compensazione con nota di credito») per il suo totale, distribuito sulle
   scadenze aperte; eliminando la nota l'incasso sparisce. Così la fattura
   stornata esce da scadenziario, solleciti e cashflow.
9. Pacchetto commercialista: includi gli XML delle note di credito
   (`documenti/commercialista.py`, oggi le segnala come «senza XML»).

**Da chiedere all'utente prima di iniziare**: numerazione delle note di
credito nel `<Numero>` dell'XML. Il controllo duplicati del SDI (errore
00404) dovrebbe considerare anche il tipo documento — verificalo sulle
specifiche tecniche in vigore — quindi «1/2026» potrebbe esistere sia come
fattura sia come nota di credito; molti commercialisti preferiscono comunque
un sezionale dedicato (es. «NC»). Proponi entrambe le opzioni e lascia
decidere.

**Accettazione**
- [ ] XML TD04 con `DatiFattureCollegate` e importi positivi, valido allo
      schema.
- [ ] Progressivo `N…` senza collisioni con quelli delle fatture
      (test su pk vicini).
- [ ] `sdi_pull` assegna RC/NS/MC alla nota giusta; reinvio dopo scarto
      con tentativo +1.
- [ ] Nota emessa: non si modifica né si elimina; un doppio clic non la
      invia due volte.
- [ ] Fattura stornata per intero: non compare più tra i crediti né nei
      solleciti.

---

## Verifica di ogni intervento

1. `venv/bin/python manage.py check` e
   `venv/bin/python manage.py makemigrations --check --dry-run` (solo le
   migrazioni previste).
2. Test Django dell'intervento più tutta la suite:
   `venv/bin/python manage.py test --parallel 1` (deve restare verde).
   Poi rimuovi i file di test creati (istruzione dell'utente).
3. `venv/bin/pyflakes` sui file toccati.
4. Prova nel browser **su una copia del database**:
   - copia consistente con l'API di backup di SQLite:
     `sqlite3.connect('db.sqlite3').backup(sqlite3.connect('/tmp/copia.sqlite3'))`;
   - un modulo di settings fuori dal repo con
     `from gestionale.settings import *` e
     `DATABASES['default']['NAME'] = '/tmp/copia.sqlite3'`;
   - `runserver` su un'altra porta con `DJANGO_SETTINGS_MODULE` e
     `PYTHONPATH` che puntano a quel modulo; dopo ogni modifica ai template
     riavvialo (con `--noreload` la cache dei template non si svuota).
   Controlla anche a 390 px di larghezza (menu, tabelle che scorrono, niente
   scroll orizzontale della pagina).
5. Aggiorna il README (sezioni «Velocità e uso quotidiano» e «Da fare») e
   la palette ⌘K se ci sono pagine o azioni nuove.
6. Riassumi all'utente cosa hai fatto, cosa resta e cosa deve fare sul
   server (migrazioni le applica già il deploy; timer systemd e nginx no).
