/* global React */
// Variante A — Minimale Professional
// Logo top-left, sottile riga gradient, molto bianco, focus sui dati

const VariantA = ({ doc }) => {
  const {
    type, number, date, dueDate,
    company, client,
    items, notes, payment,
    extraFields = {}
  } = doc;

  const subtotal = items.reduce((s, i) => s + i.qty * i.price * (1 - (i.discount || 0) / 100), 0);
  const vatRate = doc.vatRate ?? 22;
  const vat = subtotal * vatRate / 100;
  const total = subtotal + vat;

  return (
    <div className="page variant-a">
      <div className="page-inner">
        {/* Header */}
        <header className="va-header">
          <div className="va-logo-wrap">
            <img src="assets/logo-aigis.png" alt="AIGIS Lab" className="va-logo" />
          </div>
          <div className="va-doc-title">
            <div className="va-doc-type">{type}</div>
            <div className="va-doc-number">N° <span className="mono">{number}</span></div>
            <div className="va-doc-date muted">{date}</div>
          </div>
        </header>

        <div className="va-divider" />

        {/* Parties */}
        <section className="va-parties">
          <div className="va-party">
            <div className="va-party-label">Mittente</div>
            <div className="va-party-name">{company.name}</div>
            <div className="muted">{company.address}</div>
            <div className="muted">{company.city}</div>
            <div className="muted">P.IVA {company.vat}</div>
            <div className="muted">{company.email}</div>
          </div>
          <div className="va-party va-party-right">
            <div className="va-party-label">Destinatario</div>
            <div className="va-party-name">{client.name}</div>
            <div className="muted">{client.address}</div>
            <div className="muted">{client.city}</div>
            {client.vat && <div className="muted">P.IVA {client.vat}</div>}
            {client.sdi && <div className="muted">Codice SDI: <span className="mono">{client.sdi}</span></div>}
          </div>
        </section>

        {/* Meta strip */}
        {(extraFields.orderRef || dueDate || payment?.method) && (
          <section className="va-meta">
            {extraFields.orderRef && (
              <div className="va-meta-cell">
                <div className="va-meta-label">Rif. ordine</div>
                <div className="mono">{extraFields.orderRef}</div>
              </div>
            )}
            {dueDate && (
              <div className="va-meta-cell">
                <div className="va-meta-label">Scadenza</div>
                <div>{dueDate}</div>
              </div>
            )}
            {payment?.method && (
              <div className="va-meta-cell">
                <div className="va-meta-label">Pagamento</div>
                <div>{payment.method}</div>
              </div>
            )}
            {payment?.iban && (
              <div className="va-meta-cell">
                <div className="va-meta-label">IBAN</div>
                <div className="mono va-iban">{payment.iban}</div>
              </div>
            )}
          </section>
        )}

        {/* Lines */}
        <table className="line-table va-table">
          <thead>
            <tr>
              <th style={{width: '32px'}}>#</th>
              <th>Descrizione</th>
              <th className="qty" style={{width: '60px'}}>Qtà</th>
              <th className="num" style={{width: '80px'}}>Prezzo</th>
              <th className="num" style={{width: '60px'}}>Sconto</th>
              <th className="num" style={{width: '90px'}}>Totale</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it, idx) => {
              const lineTotal = it.qty * it.price * (1 - (it.discount || 0) / 100);
              return (
                <tr key={idx}>
                  <td className="muted">{idx + 1}</td>
                  <td>
                    <div className="va-item-name">{it.name}</div>
                    {it.desc && <div className="muted va-item-desc">{it.desc}</div>}
                  </td>
                  <td className="qty">{it.qty}{it.unit && <span className="muted"> {it.unit}</span>}</td>
                  <td className="num tabular">{it.price.toFixed(2)} €</td>
                  <td className="num tabular muted">{it.discount ? `${it.discount}%` : '—'}</td>
                  <td className="num tabular"><strong>{lineTotal.toFixed(2)} €</strong></td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {/* Totals */}
        <section className="va-totals-wrap">
          <div className="totals va-totals">
            <div className="row">
              <span className="muted">Imponibile</span>
              <span className="tabular">{subtotal.toFixed(2)} €</span>
            </div>
            <div className="row">
              <span className="muted">IVA {vatRate}%</span>
              <span className="tabular">{vat.toFixed(2)} €</span>
            </div>
            <div className="row grand">
              <span>Totale</span>
              <span className="tabular">{total.toFixed(2)} €</span>
            </div>
          </div>
        </section>

        {notes && (
          <section className="va-notes">
            <div className="va-meta-label">Note</div>
            <div>{notes}</div>
          </section>
        )}

        {/* Footer */}
        <footer className="va-footer">
          <div className="va-footer-bar" />
          <div className="va-footer-top">
            <div className="va-footer-content">
              <span>{company.name}</span>
              <span className="muted">·</span>
              <span className="muted">P.IVA {company.vat}</span>
              <span className="muted">·</span>
              <span className="muted">{company.email}</span>
              {company.website && <><span className="muted">·</span><span className="muted">{company.website}</span></>}
            </div>
            <div className="va-page-num">Pag. {doc.page || 1} di {doc.totalPages || 1}</div>
          </div>
        </footer>
      </div>
    </div>
  );
};

window.VariantA = VariantA;
