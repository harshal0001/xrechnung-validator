# Explanation review sheet — ruleset 2026-08-31

The English column is a rendering of **your German text**, for comprehension only. It is not a
source of truth. The only source of truth is the verbatim assertion text from the official
artefacts — see Part 1. Review means comparing your explanation to *that*, never to memory.

---

## Part 1 — Where the official rule text actually lives

Three layers, from nearest to most authoritative.

### Layer 1 — your own ruleset directory (start here)

You already extract rule text to compute `rule_text_digest`, so you already have the exact
string the validator emits. The compiled KoSIT XSLT carries every assertion's message text
keyed by rule id. For each entry in the JSON, pull that string and paste it verbatim into the
"Official text" column below. **This is the text your `what` field must be traceable to.**

Quick way to eyeball one rule from the compiled XSLT:

```bash
grep -rn 'BR-DE-15' rulesets/2026-08-31/ --include=*.xsl | head
```

### Layer 2 — the source Schematron (when you want the human-readable original)

| Rule family | Source repository | File to open |
|---|---|---|
| `BR-*`, `BR-CO-*`, `BR-CL-*` (EN 16931 core) | `ConnectingEurope/eInvoicing-EN16931` | `cii/schematron/preprocessed/EN16931-CII-validation-preprocessed.sch` and the `ubl/` equivalent |
| `BR-DE-*` (German CIUS) | `itplr-kosit/xrechnung-schematron` | the source `.sch` for the matching release |

Search the file for `id="BR-CO-10"` — the `<assert>` element's text is the rule text. It should
be byte-identical to what your Layer 1 extractor gives you. If it is not, your digest is
computing over something other than the rule text, and that is a bug worth knowing about.

> Note: the EN 16931 repo's own issue tracker documents that some rules in the specification
> text are absent from the published Schematron (issue #500, April 2026). So "the spec says"
> and "the Schematron checks" are not always the same set. Your validator can only ever claim
> the second.

### Layer 3 — BT / BG identifiers and official German terminology

`BT-10`, `BG-25` and so on are defined in the EN 16931-1 semantic model. The norm itself is
paywalled (DIN / CEN), but the **XRechnung specification PDF from KoSIT** (free, on xeinkauf.de)
lists every BT and BG with its official German name. Use it to confirm two things per entry:

1. The BT/BG number you cite really is the field you describe.
2. The German noun you use matches the spec's noun. The spec uses **Verkäufer** (Seller) and
   **Käufer** (Buyer) for the EN 16931 parties. Your entries say *Rechnungssteller* and
   *Rechnungsempfänger* — understandable, but not the standard's vocabulary, and a domain
   reviewer will notice.

### The procedure, per entry

1. Paste the verbatim official text into the sheet.
2. Split your explanation: **`what`** = only what the rule text supports; **`why`** = everything
   else (typical causes, legal context, consequences).
3. Check the BT/BG number against Layer 3.
4. Check terminology against Layer 3.
5. Have a native German speaker with accounting exposure read `why`. Record who, and when.
6. Set `reviewed_digest` = `rule_text_digest`. Serve only when they match.

---

## Part 2 — The 25 entries, side by side

Legend for the last column — things to verify against the official text before deciding:
**Y** = sentence likely belongs in `why`, not `what` · **BT** = verify the identifier ·
**T** = terminology check · **C** = check whether the rule text actually lists what you claim.

| Rule | German (yours) | English rendering | Official text (paste from Layer 1) | Check |
|---|---|---|---|---|
| **BR-02** | Die Rechnungsnummer (BT-1) fehlt. Jede Rechnung braucht eine eindeutige, fortlaufende Nummer – sie identifiziert den Vorgang in der Buchhaltung beider Seiten und ist umsatzsteuerlich vorgeschrieben. | The invoice number (BT-1) is missing. Every invoice needs a unique, sequential number – it identifies the transaction in both parties' bookkeeping and is required under VAT law. | | Y: "fortlaufend", "umsatzsteuerlich vorgeschrieben" |
| **BR-03** | Das Rechnungsdatum (BT-2) fehlt. Es bestimmt, in welchen Zeitraum die Rechnung gebucht wird und ab wann Zahlungsfristen laufen. | The invoice date (BT-2) is missing. It determines which period the invoice is booked into and from when payment deadlines run. | | Y: second sentence |
| **BR-05** | Die Rechnungswährung (BT-5) fehlt. Ohne sie sind die Beträge nicht eindeutig – 100 kann Euro oder Franken bedeuten. | The invoice currency (BT-5) is missing. Without it the amounts are ambiguous – 100 could mean euros or francs. | | Y: second sentence |
| **BR-08** | Die Anschrift des Rechnungsstellers (BG-5) fehlt. Wer die Rechnung stellt, muss mit vollständiger Adresse erkennbar sein. | The seller's postal address (BG-5) is missing. Whoever issues the invoice must be identifiable with a complete address. | | T: Rechnungssteller → Verkäufer |
| **BR-10** | Die Anschrift des Rechnungsempfängers (BG-8) fehlt. Der Empfänger muss mit vollständiger Adresse benannt sein. | The buyer's postal address (BG-8) is missing. The recipient must be named with a complete address. | | T: Rechnungsempfänger → Käufer |
| **BR-16** | Die Rechnung enthält keine einzige Rechnungsposition (BG-25). Eine Rechnung ohne Positionen sagt nicht, wofür bezahlt werden soll. Häufige Ursache: die Positionen sind beim Export aus dem Vorsystem verloren gegangen. | The invoice contains no invoice lines at all (BG-25). An invoice without lines does not say what is being paid for. Common cause: the lines were lost during export from the upstream system. | | Y: sentences 2–3 |
| **BR-CL-01** | Der Rechnungstyp ist kein zulässiger Code. Er muss aus der Codeliste UNTDID 1001 stammen – für eine normale Rechnung ist das 380, für eine Gutschrift 381. | The invoice type is not a permitted code. It must come from code list UNTDID 1001 – 380 for a standard invoice, 381 for a credit note. | | C: does the text name 380/381, or only the list? |
| **BR-CL-04** | Die Währung ist kein gültiger ISO-4217-Code. Erwartet wird ein dreistelliges Kürzel wie EUR oder CHF. | The currency is not a valid ISO 4217 code. A three-letter code such as EUR or CHF is expected. | | Y: examples |
| **BR-CL-14** | Der Ländercode ist kein gültiger ISO-3166-1-Code. Erwartet wird ein zweistelliges Kürzel wie DE oder AT. | The country code is not a valid ISO 3166-1 code. A two-letter code such as DE or AT is expected. | | Y: examples |
| **BR-CL-17** | Der Umsatzsteuer-Kategoriecode ist nicht zulässig. Er muss aus der Codeliste UNCL 5305 stammen – etwa S für den Regelsatz, AE für Reverse Charge oder Z für den Nullsatz. | The VAT category code is not permitted. It must come from code list UNCL 5305 – e.g. S for standard rate, AE for reverse charge, Z for zero rate. | | C: does the text name the codes? |
| **BR-CO-09** | Die Umsatzsteuer-Identifikationsnummer beginnt nicht mit dem Länderpräfix. Sie muss mit dem zweistelligen Ländercode geschrieben werden – für Deutschland also DE123456789, nicht 123456789. | The VAT identifier does not begin with the country prefix. It must be written with the two-letter country code – for Germany DE123456789, not 123456789. | | Y: example; BT |
| **BR-CO-10** | Die Summe der Positionsbeträge (BT-106) stimmt nicht mit den einzelnen Positionen überein. Der Kopfbetrag muss exakt der Summe aller Netto-Positionsbeträge entsprechen. Typische Ursache sind Rundungen, die pro Position anders gerechnet wurden als in der Summe. | The sum of line net amounts (BT-106) does not match the individual lines. The header total must exactly equal the sum of all line net amounts. Typical cause: rounding done differently per line than in the total. | | Y: sentence 3; BT |
| **BR-CO-13** | Der Nettogesamtbetrag (BT-109) geht nicht auf. Er muss der Summe der Positionen abzüglich Nachlässe und zuzüglich Zuschläge auf Dokumentebene entsprechen. Meist fehlt ein Nachlass oder Zuschlag in der Rechnung. | The invoice total without VAT (BT-109) does not add up. It must equal the sum of lines minus document-level allowances plus document-level charges. Usually an allowance or charge is missing from the invoice. | | Y: sentence 3; BT |
| **BR-CO-14** | Der ausgewiesene Umsatzsteuerbetrag (BT-110) stimmt nicht mit der Aufschlüsselung nach Steuersätzen überein. Die Summe der einzelnen Steuerbeträge muss den Gesamtbetrag ergeben. | The stated VAT amount (BT-110) does not match the breakdown by rate. The sum of the individual VAT amounts must equal the total. | | BT |
| **BR-CO-15** | Der Bruttogesamtbetrag (BT-112) ist nicht Nettobetrag plus Umsatzsteuer. Einer der drei Werte ist falsch – häufig, weil einer davon nachträglich geändert wurde, ohne die anderen anzupassen. | The invoice total with VAT (BT-112) is not net plus VAT. One of the three values is wrong – often because one was changed afterwards without adjusting the others. | | Y: sentence 2; BT |
| **BR-CO-16** | Der Zahlbetrag (BT-115) geht nicht auf. Er muss dem Bruttobetrag abzüglich bereits gezahlter Beträge und zuzüglich eines Rundungsbetrags entsprechen. Häufig fehlt eine bereits geleistete Anzahlung. | The amount due for payment (BT-115) does not add up. It must equal the gross total minus amounts already paid plus a rounding amount. Often a prepayment already made is missing. | | Y: sentence 3; BT |
| **BR-DE-1** | Es fehlen Angaben zur Zahlung (BG-16). Die Rechnung muss mitteilen, wie sie bezahlt werden soll – etwa per Überweisung mit IBAN, per Lastschrift oder per Karte. Ohne diese Angabe kann die Zahlung nicht ausgelöst werden. | Payment information (BG-16) is missing. The invoice must state how it is to be paid – e.g. bank transfer with IBAN, direct debit, or card. Without it the payment cannot be initiated. | | Y: sentences 2–3 |
| **BR-DE-15** | Die Käuferreferenz (BT-10) fehlt. Öffentliche Auftraggeber in Deutschland nutzen dieses Feld – meist die Leitweg-ID – um die Rechnung intern an die richtige Stelle weiterzuleiten. Ohne sie kann die Rechnung nicht zugeordnet werden und wird abgewiesen. Die Referenz steht in der Regel in der Bestellung oder im Auftragsschreiben. | The buyer reference (BT-10) is missing. German public-sector buyers use this field – usually the Leitweg-ID – to route the invoice internally to the right unit. Without it the invoice cannot be assigned and is rejected. The reference is normally in the purchase order or award letter. | | Y: sentences 2–4 (all outcome / provenance claims) |
| **BR-DE-2** | Die Kontaktdaten des Rechnungsstellers (BG-6) fehlen vollständig. Für Rückfragen zur Rechnung verlangt der deutsche Standard einen Ansprechpartner mit Name, Telefonnummer und E-Mail-Adresse. | The seller's contact details (BG-6) are entirely missing. For queries about the invoice, the German standard requires a contact person with name, phone number and email address. | | C: does BR-DE-2 list all three, or are those BR-DE-5/6/7? T |
| **BR-DE-3** | Der Ort des Rechnungsstellers (BT-37) fehlt. Die vollständige Anschrift ist umsatzsteuerlich vorgeschrieben; der Ort ist ein Pflichtbestandteil davon. | The seller's city (BT-37) is missing. The full address is required under VAT law; the city is a mandatory part of it. | | Y: sentence 2; T |
| **BR-DE-4** | Die Postleitzahl des Rechnungsstellers (BT-38) fehlt. Sie gehört zur vollständigen Anschrift, die auf einer Rechnung angegeben sein muss. | The seller's post code (BT-38) is missing. It belongs to the full address that must appear on an invoice. | | Y: sentence 2; T |
| **BR-DE-6** | Die Telefonnummer des Ansprechpartners (BT-42) fehlt. Der deutsche Standard verlangt sie, damit Rückfragen zur Rechnung ohne Umweg geklärt werden können. | The contact person's phone number (BT-42) is missing. The German standard requires it so that queries can be resolved directly. | | Y: "damit …" clause |
| **BR-DE-7** | Die E-Mail-Adresse des Ansprechpartners (BT-43) fehlt. Sie ist zusammen mit Name und Telefonnummer Pflicht, damit Rückfragen zugestellt werden können. | The contact person's email address (BT-43) is missing. Together with name and phone number it is mandatory so that queries can be delivered. | | Y: "damit …" clause |
| **BR-DE-8** | Der Ort des Rechnungsempfängers (BT-52) fehlt. Die Anschrift des Empfängers muss vollständig sein, sonst ist die Rechnung umsatzsteuerlich nicht ordnungsgemäß. | The buyer's city (BT-52) is missing. The recipient's address must be complete, otherwise the invoice is not proper for VAT purposes. | | Y: sentence 2; T |
| **BR-DE-9** | Die Postleitzahl des Rechnungsempfängers (BT-53) fehlt. Sie gehört zur vollständigen Empfängeranschrift. | The buyer's post code (BT-53) is missing. It belongs to the complete recipient address. | | T |

A pattern worth noticing: in almost every entry, **sentence one is `what` and everything after
it is `why`.** That makes the split mechanical.

---

## Part 3 — Glossary

Terms in your explanations, with the English and — where it differs — the word the XRechnung
specification uses.

### Parties and roles

| German (yours) | English | Spec term |
|---|---|---|
| Rechnungssteller | invoice issuer | **Verkäufer** (Seller) |
| Rechnungsempfänger | invoice recipient | **Käufer** (Buyer) |
| öffentliche Auftraggeber | public-sector contracting authorities | — |
| Ansprechpartner | contact person | — |

### Document and fields

| German | English | Note |
|---|---|---|
| Rechnungsnummer | invoice number | BT-1 |
| Rechnungsdatum | invoice date | BT-2 |
| Rechnungstyp | invoice type code | BT-3 |
| Rechnungswährung | invoice currency | BT-5 |
| Käuferreferenz | buyer reference | BT-10 |
| Leitweg-ID | routing identifier used by German public authorities; typically carried in BT-10 | not an EN 16931 term |
| Rechnungsposition | invoice line | BG-25 |
| Anschrift | postal address | |
| Postleitzahl (PLZ) | post code | |
| Ort | city / town | |
| Gutschrift | credit note | type code 381 |
| Bestellung | purchase order | |
| Auftragsschreiben | award letter / order confirmation | |
| Vorsystem | upstream system (usually the ERP) | |

### Amounts and tax

| German | English | Note |
|---|---|---|
| Umsatzsteuer (USt) | VAT | |
| Umsatzsteuer-Identifikationsnummer (USt-IdNr.) | VAT identifier | |
| Umsatzsteuer-Kategoriecode | VAT category code | UNCL 5305 |
| Steuersatz | tax rate | |
| Regelsatz | standard rate | code S |
| Nullsatz | zero rate | code Z |
| Reverse Charge | reverse charge | code AE |
| Positionsbetrag | line amount | |
| Netto-Positionsbetrag | line net amount | |
| Summe der Positionsbeträge | sum of line net amounts | BT-106 |
| Nettogesamtbetrag | total without VAT | BT-109 |
| Umsatzsteuerbetrag | VAT amount | BT-110 |
| Bruttogesamtbetrag | total with VAT | BT-112 |
| Zahlbetrag | amount due for payment | BT-115 |
| Nachlass | allowance / discount | |
| Zuschlag | charge / surcharge | |
| auf Dokumentebene | at document level | as opposed to line level |
| Anzahlung | prepayment / advance payment | |
| Rundungsbetrag | rounding amount | |
| Aufschlüsselung | breakdown | |
| Kopfbetrag | header amount | |

### Payment

| German | English |
|---|---|
| Angaben zur Zahlung | payment information |
| Überweisung | bank transfer |
| Lastschrift | direct debit |
| Zahlungsfrist | payment deadline / term |
| Zahlung auslösen | initiate a payment |

### Verbs and phrases you will see in rule text and explanations

| German | English |
|---|---|
| fehlt / fehlen | is / are missing |
| ist nicht zulässig | is not permitted |
| stimmt nicht überein | does not match |
| geht nicht auf | does not add up |
| muss … entsprechen | must equal / correspond to |
| muss … stammen aus | must come from |
| ist vorgeschrieben | is mandated / required |
| umsatzsteuerlich vorgeschrieben | required under VAT law |
| ordnungsgemäß | proper / compliant |
| Pflichtbestandteil | mandatory component |
| Rückfragen | queries / follow-up questions |
| zuordnen | assign / allocate |
| weiterleiten | route / forward |
| abweisen | reject |
| Codeliste | code list |
| Kürzel | abbreviation / short code |
| Buchhaltung | bookkeeping / accounting |
| gebucht werden | to be booked / posted |
| nachträglich geändert | changed afterwards |
| Häufige / Typische Ursache | common / typical cause |
