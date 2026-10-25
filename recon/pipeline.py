"""End-to-end orchestration: ingest -> data quality -> reconcile -> classify ->
explain -> persist -> evaluate."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import config as C
from .classify import classify_breaks
from .evaluate import evaluate
from .explain import LLMFn, explain_breaks
from .ingest import build_database, load_custodian_raw, load_reference, normalize_custodian
from .quality import run_all
from .reconcile import reconcile_positions


@dataclass
class RunResult:
    reference: dict
    custodian_raw: pd.DataFrame
    custodian_clean: pd.DataFrame
    remap_log: pd.DataFrame
    dq: pd.DataFrame
    results: pd.DataFrame
    breaks: pd.DataFrame
    metrics: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)


def run(raw_dir: Path = C.RAW_DIR, db_path: Path = C.DB_PATH, llm: LLMFn | None = None) -> RunResult:
    timings = {}
    t0 = time.perf_counter()

    ref = load_reference(raw_dir)
    cust_raw = load_custodian_raw(raw_dir)
    cust_clean, remap_log = normalize_custodian(cust_raw, ref["securities"], ref["accounts"])
    timings["ingest_s"] = time.perf_counter() - t0

    t = time.perf_counter()
    dq = run_all(ref, cust_raw, cust_clean)
    timings["data_quality_s"] = time.perf_counter() - t

    t = time.perf_counter()
    con = build_database(db_path, {**ref, "custodian_positions": cust_clean})
    dq.to_sql("dq_issues", con, if_exists="append", index=False)
    results = reconcile_positions(con)
    timings["load_and_reconcile_s"] = time.perf_counter() - t

    t = time.perf_counter()
    breaks = classify_breaks(results, ref["ibor_trades"], ref["securities"], dq)
    breaks = explain_breaks(breaks, llm=llm)
    timings["classify_and_explain_s"] = time.perf_counter() - t

    store = breaks.copy()
    store["evidence"] = store["evidence"].map(lambda e: json.dumps(e, default=str))
    store.to_sql("breaks", con, if_exists="append", index=False)
    con.commit()
    con.close()
    timings["total_s"] = time.perf_counter() - t0

    run_result = RunResult(ref, cust_raw, cust_clean, remap_log, dq, results, breaks, timings=timings)
    run_result.metrics = summarize(run_result, raw_dir)
    return run_result


def summarize(rr: RunResult, raw_dir: Path) -> dict:
    res, brk = rr.results, rr.breaks
    review = brk[brk["needs_review"] == 1]
    last_day = res["as_of_date"].max()
    metrics = {
        "period_start": res["as_of_date"].min(),
        "period_end": last_day,
        "business_days": int(res["as_of_date"].nunique()),
        "portfolios": int(res["portfolio_id"].nunique()),
        "securities": int(len(rr.reference["securities"])),
        "trades": int(len(rr.reference["ibor_trades"])),
        "custodian_rows_received": int(len(rr.custodian_raw)),
        "custodian_rows_loaded": int(len(rr.custodian_clean)),
        "identifiers_remapped": int(len(rr.remap_log)),
        "position_lines_reconciled": int(len(res)),
        "lines_matched": int((res["status"] == "MATCHED").sum()),
        "auto_match_rate": round(float((res["status"] == "MATCHED").mean()), 4),
        "breaks_total": int(len(brk)),
        "breaks_auto_explained_timing": int((brk["needs_review"] == 0).sum()),
        "exceptions_for_review": int(len(review)),
        "exception_rate_of_lines": round(len(review) / len(res), 4),
        "review_impact_cad_abs": round(float(review["impact_cad"].abs().sum()), 2),
        "dq_issues": int(len(rr.dq)),
        "breaks_by_category": brk["category"].value_counts().to_dict(),
        "dq_by_rule": rr.dq["rule"].value_counts().to_dict(),
        "explanation_sources": brk["explanation_src"].value_counts().to_dict(),
        "open_exceptions_last_day": int(((brk["as_of_date"] == last_day) & (brk["needs_review"] == 1)).sum()),
        "runtime_seconds": round(rr.timings["total_s"], 2),
    }
    metrics["evaluation"] = evaluate(brk, rr.dq, raw_dir)
    return metrics
