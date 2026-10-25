"""Root-cause classification of reconciliation breaks.

Rules are evaluated in a fixed order; each rule needs positive evidence
(a pending trade, a matching settled trade, a split ratio, ...). Whatever is
left over is labelled UNEXPLAINED so a person looks at it.
"""
from __future__ import annotations

import math

import pandas as pd

from . import config as C

AUTO_CLEAR = {"TIMING_UNSETTLED_TRADE"}
CATEGORY_LABELS = {
    "TIMING_UNSETTLED_TRADE": "Timing - unsettled trade",
    "TRADE_NOT_BOOKED_AT_CUSTODIAN": "Trade not booked at custodian",
    "CORPORATE_ACTION_UNPROCESSED": "Corporate action not processed",
    "PRICE_DIFFERENCE": "Price difference",
    "FX_RATE_DIFFERENCE": "FX rate difference",
    "POSITION_NOT_IN_IBOR": "Position missing in IBOR",
    "POSITION_NOT_AT_CUSTODIAN": "Position missing at custodian",
    "UNEXPLAINED_QUANTITY": "Unexplained quantity",
}
TRADE_MATCH_LOOKBACK_DAYS = 14  # calendar days


def _num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def _severity(impact: float) -> str:
    for floor, label in C.SEVERITY_BANDS:
        if abs(impact) >= floor:
            return label
    return "Low"


def _is_ratio(ratio: float) -> float | None:
    for r in C.SPLIT_RATIOS:
        if abs(ratio - r) <= 1e-6 * r:
            return r
    return None


def classify_breaks(results: pd.DataFrame, trades: pd.DataFrame, securities: pd.DataFrame,
                    dq: pd.DataFrame) -> pd.DataFrame:
    brk = results[results["status"] != "MATCHED"].copy()
    sec = securities.set_index("security_id")
    trades = trades.copy()
    trades["signed_qty"] = trades["quantity"].where(trades["side"] == "BUY", -trades["quantity"])
    by_pos = {k: g for k, g in trades.groupby(["portfolio_id", "security_id"])}
    dq_by_key = {k: g for k, g in dq.dropna(subset=["security_id"]).groupby(["as_of_date", "security_id"])}

    rows = []
    for r in brk.itertuples(index=False):
        d, p, s = r.as_of_date, r.portfolio_id, r.security_id
        iq, cq = _num(r.ibor_qty), _num(r.cust_qty)
        ip, cp = _num(r.ibor_price), _num(r.cust_price)
        diff = (iq or 0.0) - (cq or 0.0)
        tr = by_pos.get((p, s), trades.iloc[0:0])
        pending = tr[(tr["trade_date"] <= d) & (tr["settle_date"] > d)]
        pending_qty = float(pending["signed_qty"].sum())
        lookback = (pd.Timestamp(d) - pd.Timedelta(days=TRADE_MATCH_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        settled = tr[(tr["settle_date"] <= d) & (tr["settle_date"] >= lookback)]
        dq_flags = dq_by_key.get((d, s))

        info = sec.loc[s]
        ev = {
            "date": d, "portfolio": p, "security_id": s, "ticker": info["ticker"], "name": info["name"],
            "asset_class": info["asset_class"], "currency": info["currency"], "recon_status": r.status,
            "ibor_qty": iq, "cust_qty": cq, "qty_diff": round(diff, 4),
            "ibor_price": ip, "cust_price": cp,
            "ibor_mv_cad": _num(r.ibor_mv_cad), "cust_mv_cad": _num(r.cust_mv_cad),
            "mv_cad_diff": round(float(r.mv_cad_diff), 2),
        }
        if ip is not None and cp:
            ev["price_diff_pct"] = round((ip / cp - 1) * 100, 4)
        if dq_flags is not None:
            ev["dq_flags"] = [f"{x.rule}: {x.detail}" for x in dq_flags.itertuples()]

        category = None
        qty_issue = r.status in ("MISSING_AT_CUSTODIAN", "MISSING_IN_IBOR", "QTY_BREAK")
        if qty_issue and abs(pending_qty) > 0 and abs(diff - pending_qty) <= C.TOL.quantity_abs:
            category = "TIMING_UNSETTLED_TRADE"
            ev["pending_trades"] = [
                {"trade_id": t.trade_id, "side": t.side, "quantity": t.quantity, "settle_date": t.settle_date}
                for t in pending.itertuples()]
        if category is None and qty_issue:
            match = settled[(settled["signed_qty"] - diff).abs() <= C.TOL.quantity_abs]
            if len(match):
                t = match.iloc[-1]
                category = "TRADE_NOT_BOOKED_AT_CUSTODIAN"
                ev["matched_trade"] = {"trade_id": t["trade_id"], "side": t["side"], "quantity": float(t["quantity"]),
                                       "trade_date": t["trade_date"], "settle_date": t["settle_date"]}
        if category is None and r.status == "QTY_BREAK" and iq and cq:
            ratio = _is_ratio(cq / iq)
            price_close = ip is not None and cp and abs(ip - cp) / cp <= C.TOL.price_rel
            if ratio and price_close:
                category = "CORPORATE_ACTION_UNPROCESSED"
                ev["custodian_to_ibor_ratio"] = ratio
        if category is None and r.status == "MISSING_IN_IBOR":
            category = "POSITION_NOT_IN_IBOR"
        if category is None and r.status == "MISSING_AT_CUSTODIAN":
            category = "POSITION_NOT_AT_CUSTODIAN"
        if category is None and r.status == "QTY_BREAK":
            category = "UNEXPLAINED_QUANTITY"
        if category is None:  # PRICE_BREAK or MV_BREAK with matching quantity
            local_gap = abs(r.ibor_mv_local - r.cust_mv_local)
            if local_gap > C.TOL.local_mv_rel * abs(r.cust_mv_local):
                category = "PRICE_DIFFERENCE"
            else:
                category = "FX_RATE_DIFFERENCE"
                ev["ibor_fx"], ev["cust_fx"] = _num(r.ibor_fx), _num(r.cust_fx)
                ev["fx_diff"] = round(r.cust_fx - r.ibor_fx, 6)

        # A line can carry a second problem behind the first one (e.g. an unsettled
        # trade AND a bad price). Flag it so it is not auto-cleared.
        secondary = []
        if (category not in ("PRICE_DIFFERENCE", "CORPORATE_ACTION_UNPROCESSED")
                and ip is not None and cp and abs(ip - cp) / cp > C.TOL.price_rel):
            secondary.append("PRICE_DIFFERENCE")
        ifx, cfx = _num(r.ibor_fx), _num(r.cust_fx)
        if category != "FX_RATE_DIFFERENCE" and ifx and cfx and abs(ifx - cfx) > 1e-9:
            secondary.append("FX_RATE_DIFFERENCE")
            ev["ibor_fx"], ev["cust_fx"] = ifx, cfx
        if secondary:
            ev["secondary_issues"] = secondary

        if r.status == "MISSING_AT_CUSTODIAN":
            impact = _num(r.ibor_mv_cad)
        elif r.status == "MISSING_IN_IBOR":
            impact = -_num(r.cust_mv_cad)
        else:
            impact = float(r.mv_cad_diff)
        ev["impact_cad"] = round(impact, 2)
        rows.append({
            "as_of_date": d, "portfolio_id": p, "security_id": s, "status": r.status,
            "category": category, "needs_review": int(category not in AUTO_CLEAR or bool(secondary)),
            "severity": "Low" if category in AUTO_CLEAR and not secondary else _severity(impact),
            "impact_cad": round(impact, 2), "evidence": ev,
        })

    out = pd.DataFrame(rows, columns=["as_of_date", "portfolio_id", "security_id", "status", "category",
                                      "needs_review", "severity", "impact_cad", "evidence"])
    return _add_age(out, sorted(results["as_of_date"].unique()))


def _add_age(breaks: pd.DataFrame, all_dates: list[str]) -> pd.DataFrame:
    """Consecutive business days the same break (position + category) has been open."""
    pos = {d: i for i, d in enumerate(all_dates)}
    breaks = breaks.sort_values(["portfolio_id", "security_id", "category", "as_of_date"]).copy()
    ages, prev_key, prev_idx, age = [], None, None, 0
    for r in breaks.itertuples():
        key, idx = (r.portfolio_id, r.security_id, r.category), pos[r.as_of_date]
        age = age + 1 if key == prev_key and idx == prev_idx + 1 else 1
        ages.append(age)
        prev_key, prev_idx = key, idx
    breaks["age_days"] = ages
    for r_i, ev in zip(breaks.index, breaks["evidence"]):
        ev["age_days"] = int(breaks.at[r_i, "age_days"])
    return breaks.sort_values(["as_of_date", "portfolio_id", "security_id"]).reset_index(drop=True)
