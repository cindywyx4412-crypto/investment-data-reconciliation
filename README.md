# Automated Investment Data Reconciliation & Quality Pipeline

An end-to-end Investment Operations workflow in Python and SQL: it ingests messy
custodian and book-of-record files, runs data-quality checks, reconciles positions,
assigns a root cause to every break, writes plain-English explanations (optionally
with an LLM whose output is fact-checked), and delivers an Excel exception report and
an HTML dashboard.

**Live dashboard:** `docs/index.html` (enable GitHub Pages on the `docs/` folder) ·
**Write-up:** [`docs/project_report.pdf`](docs/project_report.pdf) ·
**Sample report:** [`docs/sample_reconciliation_report.xlsx`](docs/sample_reconciliation_report.xlsx)

> All data is **synthetic**. Every portfolio, security, price and FX rate is simulated;
> no real client data is used.

## Results (one simulated month)

| Measure | Result |
|---|---|
| Position lines reconciled | 2,407 (21 business days, 3 portfolios, 60 securities, 191 trades) |
| Matched automatically | 2,185 (90.8%) |
| Breaks auto-explained as settlement timing | 191 of 222 |
| Exceptions left for review | 31 (1.3% of lines), each with root cause, severity and action |
| Break detection vs ground truth | recall 100%, precision 100% |
| Root-cause accuracy vs ground truth | 100% (222 / 222) |
| Data-quality issues vs ground truth | recall 100%, precision 100% (10 / 10) |
| Robustness: 20 random seeds | 48,696 lines, 4,544 breaks, 0 missed, 100% correctly classified; DQ precision 99.0% |
| Runtime | about 1 second for the month |

The ground truth is written by the data generator, so these numbers are **measured**,
not estimated. The robustness run surfaced a real weakness: 2 false stale-price alerts
on low-volatility ETFs whose two-decimal price genuinely did not change for three days.

## How it works

```
raw files ──► 1 ingest & clean ──► 2 data quality ──► 3 reconcile (SQL) ──► 4 root cause
 (IBOR,          (pandas)            (9 rules)          (SQLite full          (rule-based,
 custodian,                                              outer join +          evidence
 prices, FX)                                             tolerances)           required)
                                                                                  │
          6 deliver (Excel with live formulas, HTML dashboard, SQLite, JSON) ◄── 5 explain
                                                                       (template or Claude,
                                                                        every figure verified)
```

1. **Ingest & clean** – parses the custodian's own format (DD/MM/YYYY dates, thousands
   separators, custodian account numbers), maps accounts and ISINs to internal IDs,
   removes exact duplicate rows, and remaps an invalid ISIN by security description.
2. **Data quality** – ISIN check digit (ISO 6166), unknown security, duplicates,
   missing / stale / jumping vendor prices (median-based so one bad print does not
   cause a second alert), quantity × price ≠ market value, negative quantity, missing FX.
3. **Reconcile** – [`sql/reconcile_positions.sql`](sql/reconcile_positions.sql): IBOR vs
   custodian full outer join; a line breaks on quantity (> 0.0001), price (> 25 bps) or
   CAD value (> max(CAD 5, 1 bp)). The same rule exists in Python and a test checks both
   agree on every line.
4. **Root cause** – [`recon/classify.py`](recon/classify.py), evaluated in order, each
   needing positive evidence: timing (gap = unsettled trades) → trade not booked at
   custodian (gap = a recently settled trade) → unprocessed corporate action (split
   ratio with matching prices) → position missing on one side → unexplained quantity →
   price vs FX difference. A second problem behind the first (e.g. timing *and* a bad
   price) is flagged and blocks auto-clearing. Severity comes from CAD impact; break age
   is tracked.
5. **Explain** – [`recon/explain.py`](recon/explain.py): a template builds the
   explanation from the evidence. With `--llm`, Claude rewrites it from the same
   evidence, then **every date and number in the reply is checked against the
   evidence**; anything unsupported means the reply is rejected and the template is used.
6. **Deliver** – Excel workbook (summary and daily trend are live `COUNTIFS` /
   `SUMPRODUCT` formulas over the detail sheets, severity colour-coding, filters),
   a self-contained HTML dashboard, the SQLite database and `metrics.json`.

## Injected scenarios (ground truth)

| Scenario | Expected result |
|---|---|
| 2-for-1 split applied by custodian, IBOR two days late | Corporate action not processed; price-jump DQ flag |
| Settled buy not booked by custodian for three days | Trade not booked at custodian |
| Vendor price stale for five days / missing / published ×100 | Price difference; stale / missing / jump DQ flags |
| Custodian uses a different USD/CAD snapshot for one portfolio | FX rate difference |
| Free-of-payment transfer-in not yet in IBOR | Position missing in IBOR |
| Row omitted from custodian file; quantity keyed +500 | Position missing at custodian; unexplained quantity |
| Duplicate row; ISIN with a wrong check digit | DQ flags; de-duplicated; ISIN remapped |

## Run it

Python 3.10+ with `pandas numpy openpyxl matplotlib reportlab` (`pip install -r requirements.txt`).

```bash
python run_pipeline.py --regenerate          # data -> output/ (db, xlsx, dashboard, metrics)
python -m unittest discover -s tests -v      # 27 unit + end-to-end tests
python scripts/robustness_check.py --seeds 20
python -m recon.report_pdf --author "Your Name"

# optional: Claude-written explanations, verified before use
export ANTHROPIC_API_KEY=...                 # model via RECON_LLM_MODEL (default claude-haiku-5-5)
python run_pipeline.py --llm
```

## Repository layout

```
recon/        config, isin, generate_data, ingest, quality, reconcile, classify,
              explain, evaluate, pipeline, report_excel, dashboard, report_pdf
sql/          schema.sql, reconcile_positions.sql
tests/        test_units.py, test_end_to_end.py
scripts/      robustness_check.py
data/raw/     generated inputs, including ground_truth_breaks.csv / ground_truth_dq.csv
docs/         project_report.pdf, index.html (dashboard), sample workbook, robustness.json
output/       created by run_pipeline.py (git-ignored)
```

## Limitations and next steps

- Synthetic scenarios have one cause per break by construction; real breaks overlap more.
- The stale-price rule should scale its run length with volatility and price precision.
- Positions only: cash and transaction-level reconciliation, bond accrued interest and
  private-market holdings are out of scope.
- Production steps: schedule the daily run, route exceptions to a ticket queue, publish the
  SQLite tables to Power BI, and track break ageing and resolution time over months.
- The live LLM call needs your own API key; in this repo it is tested with stubbed
  responses (accepted, rejected and failing cases).
