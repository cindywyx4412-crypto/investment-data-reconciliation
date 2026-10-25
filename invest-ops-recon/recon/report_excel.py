"""Month-end reconciliation workbook (openpyxl).

Summary and trend figures are live Excel formulas over the detail sheets, so
the workbook recalculates if a reviewer edits or filters the underlying rows.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import config as C
from .classify import CATEGORY_LABELS

FONT = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(name=FONT, bold=True, color="FFFFFF", size=10)
BODY_FONT = Font(name=FONT, size=10)
BOLD = Font(name=FONT, size=10, bold=True)
TITLE = Font(name=FONT, size=16, bold=True, color="1F3A5F")
SUBTLE = Font(name=FONT, size=9, italic=True, color="52514E")
THIN = Side(style="thin", color="E1E0D9")
SEVERITY_FILL = {"Critical": "F6D3D3", "High": "FBE1D6", "Medium": "FEF0CC", "Low": "F0EFEC"}
SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
MONEY = '#,##0.00;(#,##0.00);"-"'
QTY = '#,##0.####;(#,##0.####);"-"'
PCT = '0.0%;(0.0%);"-"'


def _table(ws, df: pd.DataFrame, start_row: int = 1, formats: dict | None = None, widths: dict | None = None):
    formats, widths = formats or {}, widths or {}
    for j, col in enumerate(df.columns, start=1):
        c = ws.cell(row=start_row, column=j, value=col)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for i, row in enumerate(df.itertuples(index=False), start=start_row + 1):
        for j, val in enumerate(row, start=1):
            if isinstance(val, float) and pd.isna(val):
                val = None
            c = ws.cell(row=i, column=j, value=val)
            c.font = BODY_FONT
            c.border = Border(bottom=THIN)
            fmt = formats.get(df.columns[j - 1])
            if fmt:
                c.number_format = fmt
    for j, col in enumerate(df.columns, start=1):
        ws.column_dimensions[get_column_letter(j)].width = widths.get(col, max(12, min(40, len(col) + 4)))
    ws.row_dimensions[start_row].height = 30
    last = start_row + len(df)
    ws.auto_filter.ref = f"A{start_row}:{get_column_letter(len(df.columns))}{max(last, start_row + 1)}"
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
    return last


def _severity_format(ws, col_letter: str, first: int, last: int, ncols: int):
    rng = f"A{first}:{get_column_letter(ncols)}{last}"
    for label, color in SEVERITY_FILL.items():
        ws.conditional_formatting.add(rng, FormulaRule(formula=[f'${col_letter}{first}="{label}"'],
                                                       fill=PatternFill("solid", fgColor=color)))


def build_workbook(rr, metrics: dict, path: Path) -> Path:
    sec = rr.reference["securities"].set_index("security_id")
    port = rr.reference["accounts"].set_index("portfolio_id")["portfolio_name"]

    def enrich(df):
        df = df.copy()
        df.insert(3, "Ticker", df["security_id"].map(sec["ticker"]))
        df.insert(4, "Security", df["security_id"].map(sec["name"]))
        df.insert(5, "Ccy", df["security_id"].map(sec["currency"]))
        return df

    brk = enrich(rr.breaks)
    brk["Category"] = brk["category"].map(CATEGORY_LABELS)
    brk["Needs review"] = brk["needs_review"].map({1: "Yes", 0: "No"})
    brk_cols = {"as_of_date": "Date", "portfolio_id": "Portfolio", "security_id": "Security ID",
                "status": "Recon status", "severity": "Severity", "impact_cad": "Impact (CAD)",
                "age_days": "Age (days)", "explanation": "Explanation", "suggested_action": "Suggested action",
                "explanation_src": "Explanation source"}
    history = brk.rename(columns=brk_cols)[
        ["Date", "Portfolio", "Security ID", "Ticker", "Security", "Ccy", "Recon status", "Category",
         "Needs review", "Severity", "Impact (CAD)", "Age (days)", "Explanation", "Suggested action",
         "Explanation source"]]
    exceptions = history[history["Needs review"] == "Yes"].copy()
    exceptions["_s"] = exceptions["Severity"].map(SEVERITY_ORDER)
    exceptions["_a"] = exceptions["Impact (CAD)"].abs()
    exceptions = exceptions.sort_values(["_s", "_a"], ascending=[True, False]).drop(columns=["_s", "_a", "Needs review"])

    detail = enrich(rr.results).rename(columns={
        "as_of_date": "Date", "portfolio_id": "Portfolio", "security_id": "Security ID",
        "ibor_qty": "IBOR qty", "cust_qty": "Custodian qty", "ibor_price": "IBOR price",
        "cust_price": "Custodian price", "ibor_mv_local": "IBOR MV (local)", "cust_mv_local": "Custodian MV (local)",
        "ibor_fx": "IBOR FX", "cust_fx": "Custodian FX", "ibor_mv_cad": "IBOR MV (CAD)",
        "cust_mv_cad": "Custodian MV (CAD)", "qty_diff": "Qty diff", "mv_cad_diff": "MV diff (CAD)",
        "status": "Status"})
    dq = rr.dq.rename(columns={"as_of_date": "Date", "source": "Source", "rule": "Rule", "severity": "Severity",
                               "record_key": "Record key", "security_id": "Security ID", "detail": "Detail"})

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"

    # ---------------- detail sheets first (summary formulas reference them)
    ws_ex = wb.create_sheet("Exceptions for Review")
    ws_ex["A1"], ws_ex["A1"].font = "Exceptions needing a reviewer, sorted by severity then absolute CAD impact", BOLD
    last = _table(ws_ex, exceptions, start_row=3, formats={"Impact (CAD)": MONEY},
                  widths={"Security": 30, "Category": 28, "Explanation": 80, "Suggested action": 55,
                          "Explanation source": 16, "Recon status": 20})
    for row in ws_ex.iter_rows(min_row=4, max_row=last):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    _severity_format(ws_ex, "I", 4, last, exceptions.shape[1])

    ws_h = wb.create_sheet("Break History")
    h_last = _table(ws_h, history, formats={"Impact (CAD)": MONEY},
                    widths={"Security": 30, "Category": 28, "Explanation": 70, "Suggested action": 45,
                            "Recon status": 20})
    _severity_format(ws_h, "J", 2, h_last, history.shape[1])

    ws_dq = wb.create_sheet("Data Quality")
    dq_last = _table(ws_dq, dq, widths={"Detail": 90, "Record key": 26})

    ws_d = wb.create_sheet("Recon Detail")
    d_last = _table(ws_d, detail, formats={
        "IBOR qty": QTY, "Custodian qty": QTY, "Qty diff": QTY, "IBOR price": "#,##0.0000",
        "Custodian price": "#,##0.0000", "IBOR FX": "0.0000", "Custodian FX": "0.0000",
        "IBOR MV (local)": MONEY, "Custodian MV (local)": MONEY, "IBOR MV (CAD)": MONEY,
        "Custodian MV (CAD)": MONEY, "MV diff (CAD)": MONEY}, widths={"Security": 30})

    # ---------------- daily trend (formulas over Recon Detail / Break History)
    ws_t = wb.create_sheet("Daily Trend")
    dates = sorted(rr.results["as_of_date"].unique())
    heads = ["Date", "Position lines", "Matched", "Auto-match rate", "Timing breaks (auto)", "Exceptions for review"]
    for j, h in enumerate(heads, start=1):
        c = ws_t.cell(row=1, column=j, value=h)
        c.font, c.fill, c.alignment = HEADER_FONT, HEADER_FILL, Alignment(horizontal="center", wrap_text=True)
        ws_t.column_dimensions[get_column_letter(j)].width = 16
    dcol = f"'Recon Detail'!$A$2:$A${d_last}"
    scol = f"'Recon Detail'!$S$2:$S${d_last}"
    hd = f"'Break History'!$A$2:$A${h_last}"
    hr = f"'Break History'!$I$2:$I${h_last}"
    for i, d in enumerate(dates, start=2):
        ws_t.cell(row=i, column=1, value=d)
        ws_t.cell(row=i, column=2, value=f"=COUNTIF({dcol},A{i})")
        ws_t.cell(row=i, column=3, value=f'=COUNTIFS({dcol},A{i},{scol},"MATCHED")')
        ws_t.cell(row=i, column=4, value=f"=IF(B{i}=0,0,C{i}/B{i})").number_format = PCT
        ws_t.cell(row=i, column=5, value=f'=COUNTIFS({hd},A{i},{hr},"No")')
        ws_t.cell(row=i, column=6, value=f'=COUNTIFS({hd},A{i},{hr},"Yes")')
        for j in range(1, 7):
            ws_t.cell(row=i, column=j).font = BODY_FONT
    t_last = len(dates) + 1
    ws_t.freeze_panes = "A2"
    ch = LineChart()
    ch.title, ch.y_axis.title, ch.height, ch.width = "Daily breaks", "Breaks", 8, 22
    ch.add_data(Reference(ws_t, min_col=5, max_col=6, min_row=1, max_row=t_last), titles_from_data=True)
    ch.set_categories(Reference(ws_t, min_col=1, min_row=2, max_row=t_last))
    ch.series[0].graphicalProperties.line.solidFill = "2A78D6"
    ch.series[1].graphicalProperties.line.solidFill = "EB6834"
    for s in ch.series:
        s.graphicalProperties.line.width = 22000
        s.smooth = False
    ws_t.add_chart(ch, "H2")

    # ---------------- validation vs ground truth
    ev = metrics["evaluation"]
    ws_v = wb.create_sheet("Validation")
    ws_v["A1"], ws_v["A1"].font = "Pipeline accuracy against the generator's ground truth", BOLD
    ws_v["A2"] = ("Values below are computed by recon/evaluate.py: every injected break and data-quality issue is "
                  "recorded when the synthetic data is generated, then compared with what the pipeline found.")
    ws_v["A2"].font = SUBTLE
    rows = [("Breaks expected", ev["breaks_expected"]), ("Breaks detected", ev["breaks_detected"]),
            ("Detection recall", ev["break_detection_recall"]), ("Detection precision", ev["break_detection_precision"]),
            ("Classification accuracy", ev["classification_accuracy"]),
            ("DQ issues expected", ev["dq_expected"]), ("DQ issues flagged", ev["dq_flagged"]),
            ("DQ recall", ev["dq_recall"]), ("DQ precision", ev["dq_precision"])]
    for i, (k, v) in enumerate(rows, start=4):
        ws_v.cell(row=i, column=1, value=k).font = BODY_FONT
        c = ws_v.cell(row=i, column=2, value=v)
        c.font = BODY_FONT
        if isinstance(v, float):
            c.number_format = "0.0%"
    pc = pd.DataFrame(ev["per_category"]).rename(columns={"expected_category": "Category", "correct": "Correct",
                                                          "expected": "Expected"})
    pc["Category"] = pc["Category"].map(CATEGORY_LABELS)
    _table(ws_v, pc, start_row=15, widths={"Category": 34})
    ws_v.freeze_panes = None
    ws_v.column_dimensions["A"].width = 34

    # ---------------- rules & assumptions
    ws_r = wb.create_sheet("Rules & Assumptions")
    rules = [
        ("Quantity tolerance", C.TOL.quantity_abs, "Absolute units / face value"),
        ("Price tolerance", C.TOL.price_rel, "Relative; 0.0025 = 25 bps"),
        ("Market value tolerance (absolute)", C.TOL.mv_cad_abs, "CAD"),
        ("Market value tolerance (relative)", C.TOL.mv_cad_rel, "Of custodian CAD value; 0.0001 = 1 bp"),
        ("Stale price run length", C.STALE_PRICE_DAYS, "Consecutive business days with an identical vendor price"),
        ("Price jump threshold", C.PRICE_JUMP_THRESHOLD, "Move vs median of prior 5 vendor prices"),
        ("Settlement cycle", f"T+{C.SETTLEMENT_LAG_DAYS}", "Applied to every trade in the simulation"),
        ("Data", "Synthetic", "All securities, portfolios, prices and FX are simulated (seed "
                              f"{C.SEED}); no real client data. Accrued interest is not modelled."),
    ]
    _table(ws_r, pd.DataFrame(rules, columns=["Parameter", "Value", "Note"]),
           widths={"Parameter": 34, "Value": 14, "Note": 80})

    # ---------------- summary (formulas)
    ws["A1"], ws["A1"].font = "Investment Operations - Position Reconciliation", TITLE
    ws["A2"] = (f"IBOR vs custodian | {metrics['period_start']} to {metrics['period_end']} | "
                f"{metrics['portfolios']} portfolios | synthetic data")
    ws["A2"].font = SUBTLE
    kpis = [
        ("Position lines reconciled", f"=COUNTA('Recon Detail'!$A$2:$A${d_last})", "#,##0"),
        ("Lines matched automatically", f"=COUNTIF({scol},\"MATCHED\")", "#,##0"),
        ("Auto-match rate", "=IF(B4=0,0,B5/B4)", "0.0%"),
        ("Breaks found", f"=COUNTA('Break History'!$A$2:$A${h_last})", "#,##0"),
        ("Explained as timing (auto-cleared)", f"=COUNTIF({hr},\"No\")", "#,##0"),
        ("Exceptions for review", f"=COUNTIF({hr},\"Yes\")", "#,##0"),
        ("Exceptions as % of lines", "=IF(B4=0,0,B9/B4)", "0.0%"),
        ("Gross CAD impact of exceptions",
         f"=SUMPRODUCT(('Break History'!$I$2:$I${h_last}=\"Yes\")*ABS('Break History'!$K$2:$K${h_last}))", MONEY),
        ("Data-quality issues", f"=COUNTA('Data Quality'!$A$2:$A${max(dq_last, 2)})", "#,##0"),
    ]
    ws["A3"], ws["A3"].font = "Key figures", BOLD
    for i, (label, formula, fmt) in enumerate(kpis, start=4):
        ws.cell(row=i, column=1, value=label).font = BODY_FONT
        c = ws.cell(row=i, column=2, value=formula)
        c.font, c.number_format = BOLD, fmt
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 14

    top = 15
    ws.cell(row=top, column=1, value="Breaks by root cause").font = BOLD
    for j, h in enumerate(["Category", "Breaks", "Needs review"], start=1):
        c = ws.cell(row=top + 1, column=j, value=h)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
    hc = f"'Break History'!$H$2:$H${h_last}"
    cats = list(CATEGORY_LABELS.values())
    for i, cat in enumerate(cats, start=top + 2):
        ws.cell(row=i, column=1, value=cat).font = BODY_FONT
        ws.cell(row=i, column=2, value=f"=COUNTIF({hc},A{i})").font = BODY_FONT
        ws.cell(row=i, column=3, value=f'=COUNTIFS({hc},A{i},{hr},"Yes")').font = BODY_FONT
    cat_last = top + 1 + len(cats)

    dq_top = cat_last + 2
    ws.cell(row=dq_top, column=1, value="Data-quality issues by rule").font = BOLD
    for j, h in enumerate(["Rule", "Issues"], start=1):
        c = ws.cell(row=dq_top + 1, column=j, value=h)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
    dq_rules = ["INVALID_ISIN", "UNKNOWN_SECURITY", "DUPLICATE_RECORD", "MISSING_PRICE", "STALE_PRICE",
                "PRICE_JUMP", "MV_INCONSISTENT", "NEGATIVE_QUANTITY", "MISSING_FX"]
    for i, rule in enumerate(dq_rules, start=dq_top + 2):
        ws.cell(row=i, column=1, value=rule).font = BODY_FONT
        ws.cell(row=i, column=2, value=f"=COUNTIF('Data Quality'!$C$2:$C${max(dq_last, 2)},A{i})").font = BODY_FONT

    bar = BarChart()
    bar.type, bar.title, bar.height, bar.width = "bar", "Exceptions needing review, by root cause", 8, 16
    bar.add_data(Reference(ws, min_col=3, min_row=top + 1, max_row=cat_last), titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=1, min_row=top + 2, max_row=cat_last))
    bar.series[0].graphicalProperties.solidFill = "2A78D6"
    bar.legend = None
    ws.add_chart(bar, "E3")
    ws.sheet_view.showGridLines = False

    wb.move_sheet("Daily Trend", offset=2 - wb.sheetnames.index("Daily Trend"))
    from openpyxl.workbook.properties import CalcProperties
    wb.calculation = CalcProperties(fullCalcOnLoad=True)  # Excel recalculates every formula on open
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
