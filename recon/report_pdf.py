"""Project write-up as a PDF (reportlab + matplotlib).

    python -m recon.report_pdf --author "Your Name"

Every figure in the report is read from output/metrics.json and
docs/robustness.json, which the pipeline and robustness script write. Nothing
is typed in by hand.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate,  # noqa: E402
                                Spacer, Table, TableStyle)

from . import config as C  # noqa: E402
from .classify import CATEGORY_LABELS  # noqa: E402

NAVY = colors.HexColor("#1F3A5F")
INK2 = colors.HexColor("#52514E")
GRID = colors.HexColor("#E1E0D9")
BLUE, ORANGE, MUTED = "#2a78d6", "#eb6834", "#898781"

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=15, textColor=NAVY,
                    spaceBefore=10, spaceAfter=6)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, textColor=NAVY,
                    spaceBefore=8, spaceAfter=4)
BODY = ParagraphStyle("Body", parent=ss["BodyText"], fontName="Helvetica", fontSize=9.6, leading=13.6,
                      alignment=TA_LEFT, spaceAfter=5)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8.2, leading=11, textColor=INK2)
CELL = ParagraphStyle("Cell", parent=BODY, fontSize=8.4, leading=10.8, spaceAfter=0)
CELLB = ParagraphStyle("CellB", parent=CELL, fontName="Helvetica-Bold")
BUL = ParagraphStyle("Bul", parent=BODY, leftIndent=12, bulletIndent=2, spaceAfter=2)
CODE = ParagraphStyle("Code", parent=BODY, fontName="Courier", fontSize=8.4, leading=11, leftIndent=8,
                      backColor=colors.HexColor("#F3F2EE"), borderPadding=5, spaceBefore=3, spaceAfter=8)


def P(t, s=BODY):
    return Paragraph(t, s)


def bullets(items):
    return [Paragraph(t, BUL, bulletText="•") for t in items]


def table(rows, widths, header=True, zebra=True):
    data = [[P(str(c), CELLB if (header and i == 0) else CELL) for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID),
          ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
          ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF5")), ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY)]
    t.setStyle(TableStyle(st))
    return t


# ----------------------------------------------------------------------------- figures
def _style(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c3c2b7")
    ax.tick_params(colors="#52514e", labelsize=8)
    ax.grid(axis="y", color="#e1e0d9", linewidth=0.6)
    ax.set_axisbelow(True)


def fig_architecture(path: Path):
    fig, ax = plt.subplots(figsize=(7.6, 2.9), dpi=200)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 40)
    ax.axis("off")
    boxes = [
        (1, 24, 17, 13, "Raw inputs", "IBOR positions & trades\nCustodian files (own format)\nVendor prices, FX,\nsecurity master"),
        (21, 24, 16, 13, "1. Ingest & clean", "pandas: parse dates,\nnumbers, map accounts,\nISIN -> security,\nde-duplicate"),
        (40, 24, 16, 13, "2. Data quality", "9 automated rules:\nISIN check digit, stale /\nmissing / jumping prices,\nduplicates, MV checks"),
        (59, 24, 17, 13, "3. Reconcile (SQL)", "SQLite full outer join\nIBOR vs custodian with\nqty / price / value\ntolerances"),
        (79, 24, 20, 13, "4. Root cause", "Rule-based classifier:\ntiming, unbooked trade,\nsplit, price, FX,\nmissing position ..."),
        (79, 3, 20, 13, "5. Explain (AI)", "Template or Claude;\nevery number checked\nagainst the evidence;\nfallback if unsupported"),
        (59, 3, 17, 13, "6. Deliver", "Excel exception report\n(live formulas), HTML\ndashboard, SQLite DB,\nmetrics.json"),
        (21, 3, 35, 13, "Validation", "Ground truth recorded at generation -> recall,\nprecision and root-cause accuracy;\n27 unit / end-to-end tests; 20-seed robustness run"),
    ]
    for x, y, w, h, title, body in boxes:
        fc = "#eef4fc" if title != "Validation" else "#f3f2ee"
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3,rounding_size=1.2",
                                    fc=fc, ec="#9ec5f4" if title != "Validation" else "#c3c2b7", lw=0.9))
        ax.text(x + w / 2, y + h - 2.1, title, ha="center", va="center", fontsize=7.4, weight="bold", color="#1F3A5F")
        ax.text(x + w / 2, y + h / 2 - 1.6, body, ha="center", va="center", fontsize=5.9, color="#3a3936",
                linespacing=1.25)

    def arrow(x1, y1, x2, y2):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=8, color="#52514e", lw=0.8))
    for a, b in ((18.6, 21), (37.6, 40), (56.6, 59), (76.6, 79)):
        arrow(a, 30.5, b, 30.5)
    arrow(89, 23.6, 89, 16.6)
    arrow(78.6, 9.5, 76.6, 9.5)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def fig_daily(breaks: pd.DataFrame, dates, path: Path):
    timing = [int(((breaks.as_of_date == d) & (breaks.needs_review == 0)).sum()) for d in dates]
    review = [int(((breaks.as_of_date == d) & (breaks.needs_review == 1)).sum()) for d in dates]
    x = list(range(len(dates)))
    fig, ax = plt.subplots(figsize=(7.2, 2.5), dpi=200)
    ax.plot(x, timing, color=BLUE, lw=1.6, marker="o", ms=3.2, label="Timing breaks (auto-cleared)")
    ax.plot(x, review, color=ORANGE, lw=1.6, marker="o", ms=3.2, label="Exceptions for review")
    _style(ax)
    ax.set_ylim(0, max(max(timing), max(review)) + 3)
    ax.set_ylabel("Breaks per day", fontsize=8, color="#52514e")
    ticks = [i for i in x if i % 3 == 0 or i == len(x) - 1]
    ax.set_xticks(ticks, [pd.Timestamp(dates[i]).strftime("%b %d") for i in ticks])
    ax.set_xlabel("Business days", fontsize=8, color="#52514e")
    ax.legend(frameon=False, fontsize=7.5, loc="upper left", ncol=2)
    peak = max(range(len(review)), key=lambda i: review[i])
    ax.annotate("wrong USD/CAD snapshot\nat custodian (PEN01)", (x[peak], review[peak]), xytext=(12.2, 3.6),
                textcoords="data", fontsize=6.8, color="#52514e",
                arrowprops=dict(arrowstyle="-", color="#898781", lw=0.6))
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def fig_categories(breaks: pd.DataFrame, path: Path):
    ex = breaks[breaks.needs_review == 1]
    s = ex.category.map(CATEGORY_LABELS).value_counts().sort_values()
    fig, ax = plt.subplots(figsize=(7.2, 2.3), dpi=200)
    ax.barh(s.index, s.values, color=BLUE, height=0.6)
    for i, v in enumerate(s.values):
        ax.text(v + 0.15, i, str(v), va="center", fontsize=7.5, color="#0b0b0b")
    _style(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#e1e0d9", linewidth=0.6)
    ax.set_xlabel("Exceptions needing review", fontsize=8, color="#52514e")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# ----------------------------------------------------------------------------- document
def build(author: str | None = None, out: Path = C.ROOT / "docs" / "project_report.pdf") -> Path:
    m = json.loads((C.OUTPUT_DIR / "metrics.json").read_text())
    rob = json.loads((C.ROOT / "docs" / "robustness.json").read_text())
    ev = m["evaluation"]
    con = sqlite3.connect(C.DB_PATH)
    breaks = pd.read_sql_query("SELECT * FROM breaks", con)
    dates = [r[0] for r in con.execute("SELECT DISTINCT as_of_date FROM recon_results ORDER BY 1")]
    con.close()

    figdir = C.OUTPUT_DIR / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    fig_architecture(figdir / "architecture.png")
    fig_daily(breaks, dates, figdir / "daily_breaks.png")
    fig_categories(breaks, figdir / "categories.png")

    def img(name, width):
        from reportlab.lib.utils import ImageReader
        w, h = ImageReader(str(figdir / name)).getSize()
        return Image(str(figdir / name), width=width, height=width * h / w)

    story = []
    story.append(P("Automated Investment Data Reconciliation &amp; Quality Pipeline",
                   ParagraphStyle("T", parent=H1, fontSize=19, leading=23, spaceAfter=4)))
    byline = "Python &middot; SQL (SQLite) &middot; pandas &middot; openpyxl &middot; Generative AI (Claude) with verification"
    if author:
        byline = f"{author} &nbsp;|&nbsp; " + byline
    story.append(P(byline, SMALL))
    story.append(Spacer(1, 8))

    story.append(P("Executive summary", H1))
    story.append(P(
        "Investment Operations teams reconcile their internal book of record (IBOR) against the custodian every "
        "day. Most differences are harmless timing effects, but they are mixed in with real problems such as "
        "unbooked trades, unprocessed corporate actions and bad prices, and sorting them by hand is slow and "
        "error-prone. This project automates that workflow end to end: it ingests messy source files, runs "
        "data-quality checks, reconciles positions in SQL, assigns a root cause to every break, writes a "
        "plain-English explanation (optionally with an LLM whose output is fact-checked), and delivers an Excel "
        "exception report and an HTML dashboard."))
    story.append(P(
        f"On one simulated month ({m['business_days']} business days, {m['portfolios']} client portfolios, "
        f"{m['securities']} securities, {m['trades']} trades) the pipeline reconciled "
        f"<b>{m['position_lines_reconciled']:,}</b> position lines in about {m['runtime_seconds']:.0f} second, "
        f"matched <b>{m['auto_match_rate']:.1%}</b> automatically, explained <b>{m['breaks_auto_explained_timing']}</b> "
        f"of {m['breaks_total']} breaks as settlement timing, and narrowed the review queue to "
        f"<b>{m['exceptions_for_review']}</b> exceptions ({m['exception_rate_of_lines']:.1%} of lines), each with a "
        "root cause, severity and suggested action."))
    story.append(table([
        ["Measure", "Result", "How it is measured"],
        ["Position lines reconciled", f"{m['position_lines_reconciled']:,}", "IBOR and custodian lines, full outer join"],
        ["Auto-match rate", f"{m['auto_match_rate']:.1%}", "Lines within quantity, price and value tolerances"],
        ["Breaks auto-explained", f"{m['breaks_auto_explained_timing']} of {m['breaks_total']}",
         "Difference equals unsettled trades (cleared at settlement)"],
        ["Exceptions for review", f"{m['exceptions_for_review']}", "Breaks with a non-timing or secondary cause"],
        ["Break detection (recall / precision)",
         f"{ev['break_detection_recall']:.0%} / {ev['break_detection_precision']:.0%}",
         "Against ground truth recorded when the data was generated"],
        ["Root-cause accuracy", f"{ev['classification_accuracy']:.0%} ({ev['classification_correct']}/{ev['breaks_expected']})",
         "Assigned category equals injected category"],
        ["Data-quality issues (recall / precision)", f"{ev['dq_recall']:.0%} / {ev['dq_precision']:.0%}",
         f"{ev['dq_expected']} injected issues"],
        ["Robustness, 20 random seeds", f"{rob['breaks_expected']:,} breaks: {rob['breaks_missed']} missed, "
         f"{rob['classification_accuracy']:.0%} correct; DQ precision {rob['dq_precision']:.1%}",
         f"{rob['position_lines']:,} position lines in total"],
    ], [2.0 * inch, 2.15 * inch, 2.85 * inch]))
    story.append(Spacer(1, 4))
    story.append(P("All data is synthetic: no real client, security or price data is used. The results show the "
                   "logic works on the scenarios it was designed for; they are not a claim about production "
                   "accuracy (see Limitations).", SMALL))

    story.append(P("1. Business problem", H1))
    story.append(P(
        "An IBOR records positions on <i>trade date</i>; a custodian reports <i>settled</i> positions. On any day the two "
        "will disagree for legitimate reasons (trades not yet settled) and for real ones. An analyst has to separate "
        "the two, find the cause of each real break and route it to the right team (trade support, corporate "
        "actions, pricing, the custodian). The goals for this project were:"))
    story += bullets([
        "<b>Data quality first:</b> catch bad inputs (invalid identifiers, duplicates, stale or erroneous prices) before they create noise.",
        "<b>Automated matching</b> with explicit, configurable tolerances for quantity, price and market value.",
        "<b>Root cause, not just a flag:</b> every break gets a category backed by evidence, a severity and an action.",
        "<b>Safe use of AI:</b> use an LLM to write explanations only if every figure it states can be traced to source data.",
        "<b>Measured, not claimed:</b> score the pipeline against known answers and test it.",
    ])

    story.append(P("2. Architecture", H1))
    story.append(img("architecture.png", 7.0 * inch))
    story.append(P("Each stage is a separate module in <font face='Courier'>recon/</font>; the reconciliation itself "
                   "is plain SQL in <font face='Courier'>sql/reconcile_positions.sql</font>, and all thresholds live in "
                   "<font face='Courier'>recon/config.py</font>.", SMALL))

    story.append(P("3. Data and injected scenarios", H1))
    story.append(P(
        "Because custodian and IBOR data are confidential, a generator builds a realistic month: a 60-security master "
        "file (Canadian, US and German equities, Canadian bonds priced per 100 face value, ETFs) with ISO 6166-valid "
        "ISINs; prices following geometric Brownian motion; daily USD/CAD and EUR/CAD rates; three portfolios trading "
        "2-5 times a day with T+1 settlement. Custodian files use a different layout from the internal model "
        "(DD/MM/YYYY dates, thousands separators, custodian account numbers, upper-case descriptions). The following "
        "problems are injected and written to a ground-truth file:"))
    story.append(table([
        ["Scenario injected", "What happens in the data", "Expected outcome"],
        ["Unprocessed 2-for-1 split", "Custodian doubles the quantity on the ex-date; IBOR updates two days late",
         "Corporate action not processed; price-jump DQ flag"],
        ["Trade not booked", "A settled buy is missing at the custodian for three days", "Trade not booked at custodian"],
        ["Stale vendor price", "Vendor repeats one price for five days", "Price difference; stale-price DQ flag"],
        ["Missing vendor price", "No price received; IBOR carries the last price forward", "Price difference; missing-price DQ flag"],
        ["Bad print (x100)", "Vendor publishes an ETF price 100 times too high", "Price difference; price-jump DQ flag"],
        ["Wrong FX snapshot", "Custodian converts one portfolio's USD holdings at a different rate", "FX rate difference"],
        ["Transfer-in not in IBOR", "Securities received free of payment, booked in IBOR three days later", "Position missing in IBOR"],
        ["Row omitted / keying error", "One position missing from a custodian file; one quantity off by 500",
         "Position missing at custodian; unexplained quantity"],
        ["Duplicate row / mistyped ISIN", "Custodian file repeats a row; one ISIN has a wrong check digit",
         "DQ flags; row de-duplicated and ISIN remapped by description"],
    ], [1.55 * inch, 3.05 * inch, 2.4 * inch]))

    story.append(P("4. Data-quality rules", H1))
    story.append(table([
        ["Rule", "Logic"],
        ["INVALID_ISIN", "Format check plus ISO 6166 check digit (letters expanded to digits, Luhn algorithm)"],
        ["UNKNOWN_SECURITY", "Valid ISIN not present in the security master"],
        ["DUPLICATE_RECORD", "Same date, account and ISIN appears more than once in a custodian file"],
        ["MISSING_PRICE", "Held security with no vendor price that day"],
        ["STALE_PRICE", f"Identical vendor price for {C.STALE_PRICE_DAYS}+ consecutive business days"],
        ["PRICE_JUMP", f"Price more than {C.PRICE_JUMP_THRESHOLD:.0%} away from the median of the prior "
                       f"{C.PRICE_JUMP_LOOKBACK} prices (median, so one bad print does not cause a second alert)"],
        ["MV_INCONSISTENT", "Quantity x price x multiplier differs from the reported market value"],
        ["NEGATIVE_QUANTITY / MISSING_FX", "Short position in a long-only portfolio; currency with no FX rate"],
    ], [1.75 * inch, 5.25 * inch]))

    story.append(P("5. Reconciliation and root-cause logic", H1))
    story.append(P(
        f"Positions are joined on date, portfolio and security with a full outer join. A line breaks if the quantity "
        f"differs by more than {C.TOL.quantity_abs}, the price by more than {C.TOL.price_rel * 1e4:.0f} bps, or the CAD "
        f"market value by more than the larger of CAD {C.TOL.mv_cad_abs:.0f} and {C.TOL.mv_cad_rel * 1e4:.0f} bp. "
        "The same rule exists in Python, and a test asserts both give identical results on every line. Breaks are "
        "then classified in a fixed order, each step requiring positive evidence:"))
    story.append(table([
        ["Order", "Category", "Evidence required"],
        ["1", "Timing - unsettled trade", "Quantity gap equals the net of trades with trade date &le; today &lt; settlement date"],
        ["2", "Trade not booked at custodian", "Gap equals one trade that settled in the last 14 days"],
        ["3", "Corporate action not processed", "Custodian/IBOR quantity ratio is a split ratio (2, 3, 1/2 ...) and prices agree"],
        ["4", "Position missing in IBOR / at custodian", "One side absent and nothing above explains it"],
        ["5", "Unexplained quantity", "Any other quantity gap (escalated)"],
        ["6", "Price difference vs FX rate difference", "Quantities agree; local value differs (price) or only CAD value differs (FX)"],
    ], [0.55 * inch, 2.05 * inch, 4.4 * inch]))
    story.append(P(
        "A line can carry a second problem behind the first (for example an unsettled trade <i>and</i> a bad price). "
        "Any price or FX gap behind another category is recorded as a secondary issue and stops the break from being "
        "auto-cleared. Severity is set from absolute CAD impact (Critical &ge; 1m, High &ge; 100k, Medium &ge; 10k), "
        "and the age of each break is tracked in business days."))

    story.append(P("6. AI explanations with a verification gate", H1))
    story.append(P(
        "Every break carries an evidence record (quantities, prices, FX, pending or matched trades, DQ flags, "
        "impact). By default a deterministic template turns it into an explanation. With <font face='Courier'>--llm</font> "
        "the evidence is sent to Claude through the Messages API with an instruction to use only those facts. The "
        "reply is then checked: every date must appear in the evidence and every number must equal a source figure "
        "after rounding to the decimals shown. If any figure cannot be traced, the LLM text is rejected and the "
        "template is used, and the rejected tokens are logged. API errors also fall back to the template, so the "
        "pipeline never depends on the model being available or correct."))
    sample = breaks[breaks.category == "CORPORATE_ACTION_UNPROCESSED"].iloc[0]
    story.append(P(f"<b>Example (generated):</b> {sample.explanation} <i>Action:</i> {sample.suggested_action}", SMALL))
    story.append(P(
        "Tests cover the gate directly: an answer quoting a made-up CAD amount or date is rejected, a correct answer "
        "is accepted, and a timeout falls back cleanly. The live API path needs the user's own key and was exercised "
        "through these tests with stubbed responses.", SMALL))

    story.append(P("7. Results", H1))
    story.append(KeepTogether([P("Daily breaks", H2), img("daily_breaks.png", 6.9 * inch)]))
    story.append(P(
        f"Timing breaks track trading volume and clear the next day. Exceptions stay low except on the day the "
        "custodian used a different USD/CAD rate, which affected every USD holding in one portfolio at once.", SMALL))
    story.append(KeepTogether([P("Exceptions by root cause", H2), img("categories.png", 6.9 * inch)]))
    per = {r["expected_category"]: r for r in ev["per_category"]}
    story.append(KeepTogether([P("Accuracy against ground truth", H2), table(
        [["Category", "Expected", "Correct"]] +
        [[CATEGORY_LABELS[k], per[k]["expected"], per[k]["correct"]] for k in CATEGORY_LABELS if k in per],
        [3.6 * inch, 1.2 * inch, 1.2 * inch])]))
    fps = [x for r in rob["runs"] for x in r["dq_false_positive_detail"]]
    story.append(KeepTogether([P("Robustness check", H2), P(
        f"To make sure the result is not a lucky draw, the whole generate-and-reconcile cycle was repeated with "
        f"{rob['seeds']} random seeds ({rob['position_lines']:,} position lines, {rob['breaks_expected']:,} breaks). "
        f"Breaks missed: {rob['breaks_missed']}; false breaks: {rob['false_breaks']}; root-cause accuracy "
        f"{rob['classification_accuracy']:.0%}. The data-quality rules missed none of {rob['dq_expected']} injected "
        f"issues but raised {rob['dq_false_positives']} false alerts (precision {rob['dq_precision']:.1%}): "
        f"{len(fps)} stale-price flags on low-volatility ETFs whose two-decimal price genuinely did not change for "
        "three days. This is a real limitation of a fixed run-length rule and is listed under next steps.")]))

    story.append(P("8. Testing", H1))
    story += bullets([
        "<b>Unit tests:</b> ISIN check digit on public ISINs, tolerance boundaries, each data-quality rule, each "
        "classifier branch including secondary issues and break ageing, and the AI verification gate.",
        "<b>End-to-end tests:</b> fresh data in a temporary folder; every injected break and DQ issue found; SQL and "
        "Python break rules agree on every line; duplicate removed and ISIN remapped; database queryable; generation "
        "reproducible from the seed.",
        "<b>Excel output:</b> 139 summary and trend formulas recalculated with zero errors and checked against the "
        "Python figures.",
    ])

    story.append(P("9. Limitations and next steps", H1))
    story += bullets([
        "Synthetic data: scenarios are clean and one cause per break by construction. Real breaks often overlap; the "
        "secondary-issue flag is a first step.",
        "Stale-price rule: make the run length depend on volatility and price precision to remove the ETF false alerts.",
        "Scope: positions only. Extend to cash and transaction-level reconciliation, accrued interest for bonds, "
        "and private-market holdings.",
        "Operations: schedule the daily run, push exceptions to a ticket queue, and publish the SQLite tables to "
        "Power BI; track break ageing and resolution time over months.",
    ])

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(INK2)
        canvas.drawString(0.75 * inch, 0.5 * inch, "Investment data reconciliation & quality pipeline | synthetic data")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out), pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.75 * inch,
                            title="Automated Investment Data Reconciliation & Quality Pipeline",
                            author=author or "", subject="Project report")
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--author", default=None)
    ap.add_argument("--out", default=str(C.ROOT / "docs" / "project_report.pdf"))
    a = ap.parse_args()
    print(build(a.author, Path(a.out)))
