# Handoff: Documenti aziendali AIGIS Lab (Fattura, Offerta, Ordine, Nota di Credito, DDT)

## Overview

Set di template stampabili A4 per i 5 principali documenti aziendali AIGIS Lab, da integrare in un **gestionale Django** esistente.
Tutti i documenti condividono lo stesso telaio visivo (Variante A — "Minimale Professional") basato sulla palette viola/ciano del logo, con accenti gradient sulle aree chiave: tipo documento, divider, totale finale, footer.

## About the Design Files

I file in questo bundle sono **riferimenti di design creati in HTML+React (Babel inline)** — prototipi che mostrano look, layout e gerarchia tipografica desiderati, **non codice di produzione da copiare 1:1**.
L'obiettivo è **ricreare questi template HTML come Django templates** (`.html` con `{% %}` e `{{ }}`) nell'ambiente del gestionale, usando i pattern del progetto (modelli, view, sistema di rendering PDF, asset statici).

I file React `.jsx` servono solo a documentare la struttura — vanno tradotti in template Django, non importati.

## Fidelity

**High-fidelity (hifi)** — colori, tipografia, spaziature, accenti gradient sono finali. Riproduci pixel-perfect.

## Stack consigliato per il porting

- **Template engine**: Django Templates (default)
- **Rendering PDF**: [WeasyPrint](https://weasyprint.org/) — supporta tutto il CSS usato qui (gradient, custom properties, `@page`, `position:absolute`)
  - Alternative: ReportLab (più basso livello, sconsigliato per questo design), xhtml2pdf (limitato sui gradient)
- **Asset statici**: `django.contrib.staticfiles`, font caricati localmente per render PDF offline

## File files HTML di riferimento

- `Documenti AIGIS Lab v2.html` — entry point, mostra tutti i 5 documenti come artboard nel canvas
- `components/VariantA.jsx` — **questa è la struttura del template** da replicare in Django
- `data/sample.jsx` — esempio della shape del context dict che il template si aspetta
- `styles/tokens.css` — design tokens (colori, font, scale tipografica) — copia 1:1 in `static/css/`
- `styles/variants.css` — stili specifici della Variante A (le classi `.variant-a .va-*`) — copia 1:1
- `assets/logo-aigis.png` — logo (sostituire con versione trasparente quando disponibile)

## Documenti da implementare

Cinque tipi, stesso template, campi visualizzati condizionalmente:

| Tipo | Mostra prezzi | Mostra totali/IVA | Mostra scadenza | Mostra IBAN |
|------|---------------|---------------------|-----------------|-------------|
| Fattura | ✓ | ✓ | ✓ | ✓ |
| Offerta | ✓ | ✓ | ✓ (validità) | opzionale |
| Ordine | ✓ | ✓ | — | ✓ |
| Nota di Credito | ✓ | ✓ (negativi) | — | — |
| DDT | — | — | — | — |

## Layout della pagina A4

Misure pagina: **210mm × 297mm**, padding interno **18mm verticale × 16mm orizzontale**.

Struttura verticale (top → bottom):

1. **Header** (`.va-header`) — flex row, space-between
   - Logo a sinistra (altezza 38px)
   - Block titolo a destra: tipo documento (gradient text, Space Grotesk 32px bold), numero ("N° XXXX"), data
   - `margin-bottom: 2mm`
2. **Divider** (`.va-divider`) — riga gradient orizzontale, **2px** alta, `margin-bottom: 4mm`
3. **Parties** (`.va-parties`) — grid 2 colonne, gap 14mm
   - Mittente (label viola)
   - Destinatario (label ciano)
   - `margin-bottom: 10mm`
4. **Meta strip** (`.va-meta`) — grid 4 colonne con border top+bottom 1px ink-200
   - Rif. ordine, Scadenza, Pagamento, IBAN — solo se presenti
   - `padding: 4mm 0`
5. **Tabella righe** (`.va-table`) — colonne: # / Descrizione / Qtà / Prezzo / Sconto / Totale
   - Header in maiuscoletto tracciato, ink-500
   - Righe con border-bottom ink-100
6. **Totali** (`.va-totals`) — allineato a destra, larghezza 240px
   - Imponibile / IVA / **Totale** (su pillola gradient piena, bianco, Space Grotesk 18px)
7. **Note** (`.va-notes`) — solo se presenti, background viola-50 con border-left viola-400
8. **Footer assoluto** (`.va-footer`) — `bottom: 14mm`
   - Bar gradient 2px
   - Riga: dati azienda · "Pag. X di Y" allineato a destra (viola-700)

## Design Tokens (copiare in `static/css/tokens.css`)

### Brand colors

```css
--aigis-violet-900: #4c1d95;
--aigis-violet-700: #6d28d9;
--aigis-violet-500: #8b5cf6;
--aigis-violet-400: #a78bfa;
--aigis-violet-200: #ddd6fe;
--aigis-violet-50:  #f5f3ff;

--aigis-cyan-700:   #0e7490;
--aigis-cyan-500:   #06b6d4;
--aigis-cyan-400:   #22d3ee;
--aigis-cyan-300:   #67e8f9;
--aigis-cyan-100:   #cffafe;
--aigis-cyan-50:    #ecfeff;
```

### Brand gradient (l'elemento distintivo)

```css
--aigis-gradient: linear-gradient(90deg, #8b5cf6 0%, #a78bfa 35%, #67e8f9 100%);
```

Usato su: testo tipo documento, divider, footer-bar, pillola del totale finale.

### Neutrals

```css
--ink-900: #0b0a14;  /* testo principale, bordi forti */
--ink-800: #1a1825;  /* corpo testo */
--ink-700: #2d2a3e;
--ink-600: #4b4863;
--ink-500: #6b6884;  /* labels, testo secondario */
--ink-400: #9794a8;
--ink-300: #c8c6d2;
--ink-200: #e6e4ed;  /* bordi tabella header/footer meta */
--ink-100: #f3f2f7;  /* bordi tabella righe */
--ink-50:  #faf9fc;
--paper:   #ffffff;
```

### Typography

```css
--font-sans:    'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
--font-display: 'Space Grotesk', 'Inter', sans-serif;  /* tipo documento, totale */
--font-mono:    'JetBrains Mono', 'SF Mono', ui-monospace, monospace;  /* numeri documento, IBAN */
```

Scale (calibrato per A4):

```css
--fs-xs: 9px;    /* labels uppercase */
--fs-sm: 10px;   /* descrizioni, footer */
--fs-base: 11px; /* corpo tabella */
--fs-md: 12px;   /* nomi parti */
--fs-lg: 14px;
--fs-xl: 18px;   /* totale finale */
--fs-2xl: 24px;
--fs-3xl: 32px;  /* tipo documento */
```

### Spacing — usa millimetri per consistenza con A4

`2mm` (header→divider) · `4mm` (divider→parties) · `8mm` · `10mm` · `14mm` · `18mm` (margini pagina)

### Numeri e tabelle

Sempre `font-variant-numeric: tabular-nums` (classe `.tabular`) per allineamento colonne.

## Shape del context Django

Replica la stessa struttura usata in `data/sample.jsx`:

```python
ctx = {
    'doc': {
        'type': 'Fattura',                  # str: "Fattura" | "Offerta" | ...
        'number': '2026/0142',
        'date': '24/04/2026',               # già formattato dd/mm/yyyy
        'due_date': '24/05/2026',           # opzionale
        'vat_rate': 22,
        'items': [
            {
                'name': '...',
                'desc': '...',              # opzionale
                'qty': 1,
                'unit': 'h',                # opzionale: "h" | "pz" | "forf." | "gg"
                'price': 95.00,             # Decimal o float
                'discount': 10,             # opzionale: percentuale
            },
        ],
        'notes': '...',                     # opzionale
        'payment': {
            'method': 'Bonifico · 30 gg DFFM',
            'iban': 'IT60 X054 ...',
        },
        'extra_fields': {
            'order_ref': 'ORD-2026-0089',
        },
        'page': 1,
        'total_pages': 1,
    },
    'company': {
        'name': 'AIGIS Lab S.r.l.',
        'address': '...',
        'city': '...',
        'vat': 'IT...',
        'email': '...',
        'phone': '...',
        'website': '...',
    },
    'client': {
        'name': '...', 'address': '...', 'city': '...',
        'vat': '...',                        # opzionale
        'sdi': 'M5UXCR1',                    # opzionale, codice destinatario
    },
}
```

## Architettura Django suggerita

### 1. Modello

```python
# documents/models.py
class Documento(models.Model):
    TIPO_CHOICES = [
        ('fattura', 'Fattura'),
        ('offerta', 'Offerta'),
        ('ordine', 'Ordine'),
        ('nota_credito', 'Nota di Credito'),
        ('ddt', 'Documento di Trasporto'),
    ]
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    numero = models.CharField(max_length=32)
    data = models.DateField()
    scadenza = models.DateField(null=True, blank=True)
    cliente = models.ForeignKey('Cliente', on_delete=models.PROTECT)
    aliquota_iva = models.DecimalField(max_digits=4, decimal_places=2, default=22)
    note = models.TextField(blank=True)
    rif_ordine = models.CharField(max_length=64, blank=True)
    metodo_pagamento = models.CharField(max_length=120, blank=True)
    iban = models.CharField(max_length=34, blank=True)

    def calcola_totali(self):
        imponibile = sum(r.totale_riga() for r in self.righe.all())
        iva = imponibile * self.aliquota_iva / 100
        return {
            'imponibile': imponibile,
            'iva': iva,
            'totale': imponibile + iva,
        }
```

### 2. View

```python
# documents/views.py
def documento_html(request, pk):
    doc = get_object_or_404(Documento, pk=pk)
    return render(request, 'documents/documento.html', _build_context(doc))

def documento_pdf(request, pk):
    from weasyprint import HTML, CSS
    from django.contrib.staticfiles import finders
    doc = get_object_or_404(Documento, pk=pk)
    html_string = render_to_string('documents/documento.html', _build_context(doc))
    pdf = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf(
        stylesheets=[
            CSS(finders.find('css/tokens.css')),
            CSS(finders.find('css/variants.css')),
        ]
    )
    return HttpResponse(pdf, content_type='application/pdf')
```

### 3. Template (estratto dell'header)

```django
{# templates/documents/documento.html #}
{% load static humanize %}
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="{% static 'css/tokens.css' %}">
  <link rel="stylesheet" href="{% static 'css/variants.css' %}">
</head>
<body>
<div class="page variant-a">
  <div class="page-inner">
    <header class="va-header">
      <img src="{% static 'img/logo-aigis.png' %}" class="va-logo" alt="AIGIS Lab">
      <div class="va-doc-title">
        <div class="va-doc-type">{{ doc.get_tipo_display }}</div>
        <div class="va-doc-number">N° <span class="mono">{{ doc.numero }}</span></div>
        <div class="va-doc-date muted">{{ doc.data|date:"d/m/Y" }}</div>
      </div>
    </header>

    <div class="va-divider"></div>

    {# ... vedi VariantA.jsx per il resto, replica 1:1 le classi e la struttura #}
  </div>
</div>
</body>
</html>
```

### 4. Struttura asset statici

```
static/
├── css/
│   ├── tokens.css       ← copia da styles/tokens.css
│   └── variants.css     ← copia da styles/variants.css (puoi tenere solo le regole .variant-a)
├── img/
│   └── logo-aigis.png   ← logo trasparente
└── fonts/
    ├── Inter/           ← per render PDF offline (WeasyPrint non scarica i Google Fonts)
    ├── SpaceGrotesk/
    └── JetBrainsMono/
```

Per WeasyPrint, includi nei `tokens.css` le `@font-face` puntando ai file locali (i Google Fonts non vengono fetchati nel render PDF):

```css
@font-face {
  font-family: 'Inter';
  src: url('/static/fonts/Inter/Inter-Regular.woff2') format('woff2');
  font-weight: 400;
}
/* ... idem 500, 600, 700, e per Space Grotesk e JetBrains Mono */
```

## Note pratiche WeasyPrint

- Tutto il CSS che vedi nel mock è supportato — gradient, custom properties, `position: absolute`, `@page`.
- Per la stampa multi-pagina (es. fatture lunghe con molte righe), usa `@page { size: A4; margin: 0 }` e gestisci i salti riga della tabella con `tbody { page-break-inside: auto } tr { page-break-inside: avoid }`.
- Il footer attualmente è in `position: absolute`, **bottom: 14mm** — su pagine multiple va trasformato in un `@page { @bottom-center { ... } }` per ripetersi automaticamente. Vedi [docs WeasyPrint sulla pagine](https://doc.courtbouillon.org/weasyprint/stable/api_reference.html#cssselect2.compiler.compile_selector).

## Fattura elettronica (XML SDI)

I template qui servono come **rappresentazione di cortesia leggibile** del documento. Per la fatturazione elettronica vera (XML PA o B2B verso SDI), genera l'XML separatamente dallo stesso modello — non c'è correlazione tra il render HTML e lo schema XML.

Library suggerita: `python-fattura-elettronica-api` o `pyfeb`.

## Cosa NON è ancora coperto (lasciato al developer)

- QR code di pagamento (EPC SEPA): l'utente lo ha esplicitamente posticipato
- Versione bilingue IT/EN
- Watermark "BOZZA" / "DEFINITIVO"
- Riquadro firma cliente per accettazione offerte
- Marca da bollo virtuale
- Multi-pagina con header/footer ripetuti
- Generazione XML SDI

Se servono in futuro, le hook ci sono già: il context dict è estendibile e le classi CSS della Variante A sono indipendenti dal layout di pagina.

## Files inclusi in questo bundle

| File | Cosa è |
|------|--------|
| `Documenti AIGIS Lab v2.html` | Entry point, mostra i 5 documenti nel canvas |
| `components/VariantA.jsx` | **Struttura del template da replicare in Django** |
| `data/sample.jsx` | Shape del context dict |
| `styles/tokens.css` | Design tokens — copia 1:1 in static/css/ |
| `styles/variants.css` | Stili Variante A — copia 1:1 (puoi rimuovere `.variant-b` e `.variant-c` se presenti, non servono) |
| `assets/logo-aigis.png` | Logo (placeholder, sostituire con versione trasparente) |
| `design-canvas.jsx` | Solo per la presentazione del canvas — NON serve nel porting |

## Quick start per Claude Code

1. Apri `Documenti AIGIS Lab v2.html` per vedere come deve apparire il risultato finale
2. Leggi `components/VariantA.jsx` — è la "mappa" della struttura
3. Copia `styles/tokens.css` e `styles/variants.css` in `static/css/` del progetto Django
4. Crea l'app `documents` con i modelli sopra suggeriti
5. Crea il template `documents/documento.html` traducendo VariantA.jsx in sintassi Django
6. Implementa le due view (HTML preview + PDF download via WeasyPrint)
7. Verifica il PDF rendering con WeasyPrint — confronta con gli artboard del canvas
