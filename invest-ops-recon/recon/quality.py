"""Automated data-quality rules run on the inputs before reconciliation.

Each rule returns rows with the same columns, so new rules can be added
without touching the rest of the pipeline.
"""
from __future__ import annotations

import pandas as pd

from . import config as C
from .isin import is_valid_isin

COLUMNS = ["as_of_date", "source", "rule", "severity", "record_key", "security_id", "detail"]


def _issues(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLUMNS)


def check_isin_validity(cust_raw: pd.DataFrame) -> pd.DataFrame:
    bad = cust_raw[~cust_raw["isin"].map(is_valid_isin)]
    return _issues([{
        "as_of_date": r.as_of_date, "source": "custodian", "rule": "INVALID_ISIN", "severity": "High",
        "record_key": r.isin, "security_id": None,
        "detail": f"ISIN '{r.isin}' fails ISO 6166 check-digit validation ({r.source_file} row {r.source_row}).",
    } for r in bad.itertuples()])


def check_unknown_securities(cust_raw: pd.DataFrame, securities: pd.DataFrame) -> pd.DataFrame:
    known = set(securities["isin"])
    unk = cust_raw[cust_raw["isin"].map(is_valid_isin) & ~cust_raw["isin"].isin(known)]
    return _issues([{
        "as_of_date": r.as_of_date, "source": "custodian", "rule": "UNKNOWN_SECURITY", "severity": "High",
        "record_key": r.isin, "security_id": None,
        "detail": f"Valid ISIN '{r.isin}' not found in security master ({r.source_file} row {r.source_row}).",
    } for r in unk.itertuples()])


def check_duplicates(cust_raw: pd.DataFrame) -> pd.DataFrame:
    key = ["as_of_date", "custodian_account", "isin"]
    dup = cust_raw[cust_raw.duplicated(subset=key, keep="first")]
    return _issues([{
        "as_of_date": r.as_of_date, "source": "custodian", "rule": "DUPLICATE_RECORD", "severity": "Medium",
        "record_key": f"{r.custodian_account}|{r.isin}", "security_id": None,
        "detail": f"Duplicate position row for {r.custodian_account} / {r.isin} ({r.source_file} row {r.source_row}); "
                  "second copy excluded from reconciliation.",
    } for r in dup.itertuples()])


def check_missing_prices(vendor: pd.DataFrame, ibor_positions: pd.DataFrame) -> pd.DataFrame:
    """A held security with no vendor price that day."""
    held = ibor_positions[["as_of_date", "security_id"]].drop_duplicates()
    priced = vendor.rename(columns={"price_date": "as_of_date"})[["as_of_date", "security_id"]]
    m = held.merge(priced, how="left", on=["as_of_date", "security_id"], indicator=True)
    miss = m[m["_merge"] == "left_only"]
    return _issues([{
        "as_of_date": r.as_of_date, "source": "vendor_prices", "rule": "MISSING_PRICE", "severity": "High",
        "record_key": r.security_id, "security_id": r.security_id,
        "detail": "No vendor price for a held security; IBOR carried the previous price forward.",
    } for r in miss.itertuples()])


def check_stale_prices(vendor: pd.DataFrame, run_length: int = C.STALE_PRICE_DAYS) -> pd.DataFrame:
    """Same price published for >= run_length consecutive vendor dates."""
    rows = []
    for sid, g in vendor.sort_values("price_date").groupby("security_id"):
        changed = g["price"].ne(g["price"].shift())
        run = changed.cumsum()
        streak = g.groupby(run).cumcount() + 1
        for r, n in zip(g.itertuples(), streak):
            if n >= run_length:
                rows.append({
                    "as_of_date": r.price_date, "source": "vendor_prices", "rule": "STALE_PRICE",
                    "severity": "Medium", "record_key": sid, "security_id": sid,
                    "detail": f"Price {r.price:,.4f} unchanged for {n} consecutive business days.",
                })
    return _issues(rows)


def check_price_jumps(vendor: pd.DataFrame, threshold: float = C.PRICE_JUMP_THRESHOLD,
                      lookback: int = C.PRICE_JUMP_LOOKBACK) -> pd.DataFrame:
    """Price more than `threshold` away from the median of the prior `lookback` prices.

    The median makes the rule robust: one bad print does not trigger a second
    false alarm the following day.
    """
    rows = []
    for sid, g in vendor.sort_values("price_date").groupby("security_id"):
        ref = g["price"].shift(1).rolling(lookback, min_periods=3).median()
        move = g["price"] / ref - 1
        for r, ref_px, mv in zip(g.itertuples(), ref, move):
            if pd.notna(mv) and abs(mv) > threshold:
                rows.append({
                    "as_of_date": r.price_date, "source": "vendor_prices", "rule": "PRICE_JUMP",
                    "severity": "High", "record_key": sid, "security_id": sid,
                    "detail": f"Price {r.price:,.4f} is {mv:+.1%} vs trailing median {ref_px:,.4f} "
                              "(possible bad print or corporate action).",
                })
    return _issues(rows)


def check_mv_consistency(positions: pd.DataFrame, securities: pd.DataFrame, source: str) -> pd.DataFrame:
    """quantity x price x multiplier should equal the reported local market value."""
    m = positions.merge(securities[["security_id", "multiplier"]], on="security_id")
    calc = m["quantity"] * m["price"] * m["multiplier"]
    gap = (calc - m["market_value_local"]).abs()
    bad = m[gap > (C.MV_CONSISTENCY_REL * m["market_value_local"].abs()).clip(lower=1.0)]
    return _issues([{
        "as_of_date": r.as_of_date, "source": source, "rule": "MV_INCONSISTENT", "severity": "Medium",
        "record_key": f"{r.portfolio_id}|{r.security_id}", "security_id": r.security_id,
        "detail": f"Reported market value {r.market_value_local:,.2f} does not equal quantity x price.",
    } for r in bad.itertuples()])


def check_negative_quantities(positions: pd.DataFrame, source: str) -> pd.DataFrame:
    bad = positions[positions["quantity"] < 0]
    return _issues([{
        "as_of_date": r.as_of_date, "source": source, "rule": "NEGATIVE_QUANTITY", "severity": "High",
        "record_key": f"{r.portfolio_id}|{r.security_id}", "security_id": r.security_id,
        "detail": f"Negative quantity {r.quantity:,.2f} in a long-only portfolio.",
    } for r in bad.itertuples()])


def check_missing_fx(fx: pd.DataFrame, positions: pd.DataFrame, securities: pd.DataFrame) -> pd.DataFrame:
    need = positions.merge(securities[["security_id", "currency"]], on="security_id")[["as_of_date", "currency"]]
    need = need.drop_duplicates()
    have = fx.rename(columns={"rate_date": "as_of_date"})[["as_of_date", "currency"]]
    m = need.merge(have, how="left", on=["as_of_date", "currency"], indicator=True)
    miss = m[m["_merge"] == "left_only"]
    return _issues([{
        "as_of_date": r.as_of_date, "source": "fx_rates", "rule": "MISSING_FX", "severity": "High",
        "record_key": r.currency, "security_id": None, "detail": f"No {r.currency}/CAD rate for this date.",
    } for r in miss.itertuples()])


def run_all(ref: dict[str, pd.DataFrame], cust_raw: pd.DataFrame, cust_clean: pd.DataFrame) -> pd.DataFrame:
    sec = ref["securities"]
    parts = [
        check_isin_validity(cust_raw),
        check_unknown_securities(cust_raw, sec),
        check_duplicates(cust_raw),
        check_missing_prices(ref["vendor_prices"], ref["ibor_positions"]),
        check_stale_prices(ref["vendor_prices"]),
        check_price_jumps(ref["vendor_prices"]),
        check_mv_consistency(cust_clean, sec, "custodian"),
        check_mv_consistency(ref["ibor_positions"], sec, "ibor"),
        check_negative_quantities(cust_clean, "custodian"),
        check_negative_quantities(ref["ibor_positions"], "ibor"),
        check_missing_fx(ref["fx_rates"], ref["ibor_positions"], sec),
    ]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return _issues([])
    out = pd.concat(parts, ignore_index=True)
    return out.sort_values(["as_of_date", "rule", "record_key"]).reset_index(drop=True)
