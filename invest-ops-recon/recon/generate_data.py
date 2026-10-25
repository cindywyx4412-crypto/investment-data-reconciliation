"""Generate one month of synthetic investment-operations data.

Real custodian and book-of-record data is confidential, so this module builds a
realistic stand-in: three client portfolios, a 60-security master file with
valid ISINs, daily prices and FX, an IBOR (trade-date) trade blotter, and daily
custodian position files in the custodian's own messy format.

Known operational problems are injected on purpose (an unprocessed stock split,
a trade the custodian never booked, stale and bad vendor prices, a wrong FX
snapshot, duplicate rows, a mistyped ISIN, ...). Each one is written to a
ground-truth file so the pipeline's detection and classification can be
measured objectively instead of eyeballed.

Everything is driven by a fixed random seed, so every run is reproducible.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from .isin import make_isin
from .tolerance import line_status

PREFIXES = ["Northern", "Maple", "Laurentian", "Pacific", "Atlantic", "Prairie",
            "Granite", "Boreal", "Harbour", "Summit", "Cedar", "Aurora", "Fraser",
            "Kingsway", "Tundra"]
SECTORS = ["Energy", "Rail", "Financial", "Telecom", "Utilities", "Mining",
           "Insurance", "Software", "Health", "Logistics", "Foods", "Realty"]

# (asset class, currency, ISIN country, number of securities)
SECURITY_SPEC = [
    ("Equity", "CAD", "CA", 18),
    ("Equity", "USD", "US", 14),
    ("Equity", "EUR", "DE", 4),
    ("Bond", "CAD", "CA", 14),
    ("ETF", "CAD", "CA", 6),
    ("ETF", "USD", "US", 4),
]
DAILY_VOL = {"Equity": 0.015, "ETF": 0.008, "Bond": 0.002}

ACCOUNTS = [
    ("PEN01", "Public Service Pension Plan (simulated)", "CUST-884201"),
    ("INS01", "Workplace Insurance Reserve Fund (simulated)", "CUST-884202"),
    ("MUN01", "Municipal Employees Pension Plan (simulated)", "CUST-884203"),
]

# Day indexes (0-based business days in the month) for each injected scenario
SCN = {
    "qty_mismatch_day": 2,
    "unbooked_trade_day": 3,          # trade date; custodian books it late
    "unbooked_fix_day": 7,
    "missing_price_day": 5,
    "duplicate_row_day": 6,
    "split_day": 8,                   # custodian + market apply 2:1 split
    "split_ibor_fix_day": 10,         # IBOR processes it two days late
    "transfer_in_day": 9,             # custodian receives securities free of payment
    "transfer_ibor_fix_day": 12,
    "stale_from_day": 11,             # vendor keeps publishing this day's price ...
    "stale_to_day": 15,               # ... through this day (inclusive)
    "invalid_isin_day": 15,
    "bad_price_day": 17,              # vendor price published x100
    "fx_day": 18,                     # custodian uses a wrong USD/CAD snapshot
    "dropped_row_day": 19,            # custodian file omits one position
}
FX_SHOCK = 0.0045


def business_days() -> pd.DatetimeIndex:
    return pd.bdate_range(C.SIM_START, C.SIM_END, freq="C", holidays=C.HOLIDAYS)


def _lot(asset_class: str) -> int:
    return 1000 if asset_class == "Bond" else 100


def build_security_master(rng: np.random.Generator) -> pd.DataFrame:
    issuers = [f"{p} {s}" for p in PREFIXES for s in SECTORS]
    order = rng.permutation(len(issuers))
    rows, used_tickers, used_nsin = [], set(), set()
    i = 0
    for asset_class, ccy, country, n in SECURITY_SPEC:
        for _ in range(n):
            issuer = issuers[order[i]]
            i += 1
            w = issuer.split()
            if asset_class == "Bond":
                coupon = float(rng.choice([2.5, 3.0, 3.25, 3.75, 4.0, 4.25, 4.5, 5.0]))
                year = int(rng.integers(2028, 2040))
                name = f"{issuer} {coupon:.2f}% {year}"
                base = (w[0][:2] + w[1][0]).upper() + str(year)[-2:]
                start = rng.uniform(92, 104)
            elif asset_class == "ETF":
                name = f"{issuer} Index ETF"
                base = "X" + (w[0][:2] + w[1][0]).upper()
                start = rng.uniform(20, 60)
            else:
                name = f"{issuer} Inc."
                base = (w[0][:2] + w[1][:2]).upper()
                start = rng.uniform(15, 180)
            ticker, k = base, 1
            while ticker in used_tickers:
                ticker, k = f"{base}{k}", k + 1
            used_tickers.add(ticker)
            nsin = "".join(rng.choice(list("0123456789"), size=9))
            while nsin in used_nsin:
                nsin = "".join(rng.choice(list("0123456789"), size=9))
            used_nsin.add(nsin)
            rows.append({
                "security_id": f"SEC{len(rows) + 1:03d}",
                "isin": make_isin(country, nsin),
                "ticker": ticker,
                "name": name,
                "asset_class": asset_class,
                "currency": ccy,
                "multiplier": 0.01 if asset_class == "Bond" else 1.0,
                "start_price": start,
            })
    return pd.DataFrame(rows)


def simulate_true_prices(master, days, rng) -> pd.DataFrame:
    paths = {}
    for r in master.itertuples():
        vol = DAILY_VOL[r.asset_class]
        log_returns = vol * rng.standard_normal(len(days)) - 0.5 * vol ** 2  # GBM step
        log_returns[0] = 0.0                                                 # day 0 = start price
        paths[r.security_id] = r.start_price * np.exp(np.cumsum(log_returns))
    return pd.DataFrame(paths, index=days)


def round_prices(prices: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    out = prices.copy()
    for r in master.itertuples():
        out[r.security_id] = out[r.security_id].round(3 if r.asset_class == "Bond" else 2)
    return out


def simulate_fx(days, rng) -> pd.DataFrame:
    fx = pd.DataFrame(index=days)
    fx["CAD"] = 1.0
    for ccy, start in (("USD", 1.3650), ("EUR", 1.5050)):
        shocks = rng.standard_normal(len(days)) * 0.003
        shocks[0] = 0.0
        fx[ccy] = (start * np.exp(np.cumsum(shocks))).round(4)
    return fx


def generate(out_dir: Path = C.RAW_DIR, seed: int = C.SEED) -> dict:
    """Generate all raw input files into out_dir and return a small summary."""
    rng = np.random.default_rng(seed)
    days = business_days()
    n_days = len(days)
    out_dir = Path(out_dir)
    (out_dir / "custodian").mkdir(parents=True, exist_ok=True)

    master = build_security_master(rng)
    sec = master.set_index("security_id")
    true_px = simulate_true_prices(master, days, rng)
    fx = simulate_fx(days, rng)
    portfolios = [a[0] for a in ACCOUNTS]

    # ---- opening (settled) holdings --------------------------------------------
    opening = {}
    for p in portfolios:
        held = rng.choice(master["security_id"], size=int(rng.integers(30, 41)), replace=False)
        for s in held:
            ac = sec.at[s, "asset_class"]
            hi = {"Equity": 500, "ETF": 200, "Bond": 5000}[ac]
            opening[(p, s)] = float(int(rng.integers(10, hi)) * _lot(ac))

    def holders(s):
        return [p for p in portfolios if opening.get((p, s), 0) > 0]

    # ---- pick scenario securities (distinct, deterministic) ---------------------
    used: set[str] = set()

    def pick(asset_class, ccy, min_holders=1, portfolio=None, held=True):
        cands = master[(master.asset_class == asset_class) & (master.currency == ccy)]["security_id"]
        for s in cands:
            if s in used:
                continue
            if portfolio is not None:
                if held != ((portfolio, s) in opening):
                    continue
            elif len(holders(s)) < min_holders:
                continue
            used.add(s)
            return s
        raise RuntimeError(f"no candidate for {asset_class}/{ccy}")

    split_sec = pick("Equity", "CAD", min_holders=2)
    stale_sec = pick("Equity", "USD", min_holders=1)
    missing_px_sec = pick("Equity", "CAD", min_holders=1)
    bad_px_sec = pick("ETF", "CAD", min_holders=1)
    unbooked_sec = pick("Equity", "CAD", portfolio="INS01")
    qty_sec = pick("Equity", "CAD", portfolio="MUN01")
    transfer_sec = pick("Equity", "USD", portfolio="MUN01", held=False)
    dropped_sec = pick("Equity", "CAD", portfolio="PEN01")

    # Trades are blocked for scenario securities so every injected issue has a
    # single, unambiguous expected category.
    security_locks = {split_sec, stale_sec, missing_px_sec, bad_px_sec}
    pair_locks = {("INS01", unbooked_sec), ("MUN01", qty_sec), ("MUN01", transfer_sec),
                  ("PEN01", dropped_sec)}
    usd_secs = set(master.loc[master.currency == "USD", "security_id"])
    fx_lock_days = {SCN["fx_day"] - 1, SCN["fx_day"]}

    # ---- true prices: apply the 2-for-1 split from split_day -----------------------
    true_px.loc[days[SCN["split_day"]]:, split_sec] /= 2.0
    true_px = round_prices(true_px, master)

    # ---- IBOR trade blotter ----------------------------------------------------------
    bday = pd.offsets.CustomBusinessDay(holidays=C.HOLIDAYS)
    ibor_state = dict(opening)
    trades = []
    for d_idx, d in enumerate(days):
        for p in portfolios:
            n_trades = int(rng.integers(2, 6))
            chosen = set()
            for _ in range(n_trades):
                held_now = [s for (pp, s), q in ibor_state.items() if pp == p and q > 0]
                if rng.random() < 0.7 and held_now:
                    s = str(rng.choice(held_now))
                else:
                    s = str(rng.choice(master["security_id"]))
                if (s in chosen or s in security_locks or (p, s) in pair_locks
                        or (p == "PEN01" and s in usd_secs and d_idx in fx_lock_days)):
                    continue
                chosen.add(s)
                ac = sec.at[s, "asset_class"]
                lot = _lot(ac)
                cur = ibor_state.get((p, s), 0.0)
                if cur > 0 and rng.random() < 0.45:
                    side = "SELL"
                    qty = float(min(cur, int(rng.integers(1, max(2, cur / lot * 0.6))) * lot))
                    if rng.random() < 0.08:
                        qty = cur  # occasional full exit
                else:
                    side = "BUY"
                    hi = {"Equity": 120, "ETF": 80, "Bond": 1500}[ac]
                    qty = float(int(rng.integers(5, hi)) * lot)
                trades.append({
                    "trade_id": f"T{d:%Y%m%d}-{p}-{len(trades) + 1:05d}",
                    "portfolio_id": p, "security_id": s, "side": side, "quantity": qty,
                    "price": float(true_px.at[d, s]), "trade_date": d,
                    "settle_date": d + C.SETTLEMENT_LAG_DAYS * bday,
                })
                ibor_state[(p, s)] = cur + (qty if side == "BUY" else -qty)

    # Forced scenario: a buy the custodian books late (settles day 4, booked day 7)
    ub_day = days[SCN["unbooked_trade_day"]]
    trades.append({
        "trade_id": f"T{ub_day:%Y%m%d}-INS01-UNBKD", "portfolio_id": "INS01",
        "security_id": unbooked_sec, "side": "BUY", "quantity": 2500.0,
        "price": float(true_px.at[ub_day, unbooked_sec]), "trade_date": ub_day,
        "settle_date": ub_day + C.SETTLEMENT_LAG_DAYS * bday,
    })
    tr = pd.DataFrame(trades)
    tr["signed_qty"] = np.where(tr.side == "BUY", tr.quantity, -tr.quantity)
    tr["cust_effective_date"] = tr["settle_date"]
    tr.loc[tr.trade_id.str.endswith("UNBKD"), "cust_effective_date"] = days[SCN["unbooked_fix_day"]]

    # ---- vendor prices (what IBOR uses) -------------------------------------------
    vendor = true_px.copy()
    stale_days = days[SCN["stale_from_day"]: SCN["stale_to_day"] + 1]
    vendor.loc[stale_days, stale_sec] = true_px.at[days[SCN["stale_from_day"]], stale_sec]
    vendor.loc[days[SCN["bad_price_day"]], bad_px_sec] = round(true_px.at[days[SCN["bad_price_day"]], bad_px_sec] * 100, 2)
    vendor_long = vendor.stack().rename("price").reset_index()
    vendor_long.columns = ["price_date", "security_id", "price"]
    vendor_long = vendor_long[~((vendor_long.price_date == days[SCN["missing_price_day"]])
                                & (vendor_long.security_id == missing_px_sec))]
    # IBOR carries the last available vendor price forward when one is missing
    ibor_px = vendor.copy()
    ibor_px.loc[days[SCN["missing_price_day"]], missing_px_sec] = vendor.at[days[SCN["missing_price_day"] - 1], missing_px_sec]

    # ---- daily positions ------------------------------------------------------------
    pairs = set(opening) | set(zip(tr.portfolio_id, tr.security_id)) | {("MUN01", transfer_sec)}
    ibor_rows, cust_rows, truth = [], [], []
    for d_idx, d in enumerate(days):
        ibor_cum = tr[tr.trade_date <= d].groupby(["portfolio_id", "security_id"]).signed_qty.sum()
        cust_cum = tr[tr.cust_effective_date <= d].groupby(["portfolio_id", "security_id"]).signed_qty.sum()
        pend = tr[(tr.trade_date <= d) & (tr.settle_date > d)].groupby(["portfolio_id", "security_id"]).signed_qty.sum()
        for (p, s) in sorted(pairs):
            ccy, mult = sec.at[s, "currency"], sec.at[s, "multiplier"]
            iq = opening.get((p, s), 0.0) + float(ibor_cum.get((p, s), 0.0))
            cq = opening.get((p, s), 0.0) + float(cust_cum.get((p, s), 0.0))
            if s == split_sec:
                if d_idx >= SCN["split_day"]:
                    cq *= 2
                if d_idx >= SCN["split_ibor_fix_day"]:
                    iq *= 2
            if (p, s) == ("MUN01", transfer_sec):
                if d_idx >= SCN["transfer_in_day"]:
                    cq += 15000.0
                if d_idx >= SCN["transfer_ibor_fix_day"]:
                    iq += 15000.0
            if (p, s) == ("MUN01", qty_sec) and d_idx == SCN["qty_mismatch_day"]:
                cq += 500.0  # keying error at the custodian
            drop_cust = (p, s) == ("PEN01", dropped_sec) and d_idx == SCN["dropped_row_day"]

            c_fx = float(fx.at[d, ccy])
            if p == "PEN01" and ccy == "USD" and d_idx == SCN["fx_day"]:
                c_fx = round(c_fx + FX_SHOCK, 4)
            i_px, c_px = float(ibor_px.at[d, s]), float(true_px.at[d, s])
            i_mv = round(iq * i_px * mult, 2)
            c_mv = round(cq * c_px * mult, 2)
            i_row = c_row = None
            if abs(iq) > 1e-9:
                i_row = {"as_of_date": d, "portfolio_id": p, "security_id": s, "quantity": iq,
                         "price": i_px, "fx_rate": float(fx.at[d, ccy]), "market_value_local": i_mv,
                         "market_value_cad": round(i_mv * float(fx.at[d, ccy]), 2)}
                ibor_rows.append(i_row)
            if abs(cq) > 1e-9 and not drop_cust:
                c_row = {"date": d, "portfolio_id": p, "security_id": s, "qty": cq, "price": c_px,
                         "fx": c_fx, "mv_local": c_mv, "mv_cad": round(c_mv * c_fx, 2)}
                cust_rows.append(c_row)

            # ---- ground truth for this line -----------------------------------------
            if i_row is None and c_row is None:
                continue
            status = line_status(
                i_row["quantity"] if i_row else None, c_row["qty"] if c_row else None,
                i_row["price"] if i_row else None, c_row["price"] if c_row else None,
                i_row["market_value_cad"] if i_row else None, c_row["mv_cad"] if c_row else None)
            if status == "MATCHED":
                continue
            if s == split_sec and SCN["split_day"] <= d_idx < SCN["split_ibor_fix_day"]:
                cat, scen = "CORPORATE_ACTION_UNPROCESSED", "2-for-1 split applied by custodian, not yet in IBOR"
            elif (p, s) == ("INS01", unbooked_sec) and SCN["unbooked_trade_day"] < d_idx < SCN["unbooked_fix_day"]:
                cat, scen = "TRADE_NOT_BOOKED_AT_CUSTODIAN", "Settled buy not booked by custodian"
            elif (p, s) == ("MUN01", transfer_sec) and SCN["transfer_in_day"] <= d_idx < SCN["transfer_ibor_fix_day"]:
                cat, scen = "POSITION_NOT_IN_IBOR", "Free-of-payment transfer-in not recorded in IBOR"
            elif (p, s) == ("MUN01", qty_sec) and d_idx == SCN["qty_mismatch_day"]:
                cat, scen = "UNEXPLAINED_QUANTITY", "Custodian keying error (+500 shares)"
            elif drop_cust:
                cat, scen = "POSITION_NOT_AT_CUSTODIAN", "Position omitted from custodian file"
            elif s == stale_sec and d_idx in range(SCN["stale_from_day"] + 1, SCN["stale_to_day"] + 1):
                cat, scen = "PRICE_DIFFERENCE", "Stale vendor price"
            elif s == missing_px_sec and d_idx == SCN["missing_price_day"]:
                cat, scen = "PRICE_DIFFERENCE", "Missing vendor price carried forward"
            elif s == bad_px_sec and d_idx == SCN["bad_price_day"]:
                cat, scen = "PRICE_DIFFERENCE", "Vendor price published x100"
            elif p == "PEN01" and ccy == "USD" and d_idx == SCN["fx_day"]:
                cat, scen = "FX_RATE_DIFFERENCE", "Custodian used a different USD/CAD snapshot"
            elif abs(float(pend.get((p, s), 0.0))) > 0:
                cat, scen = "TIMING_UNSETTLED_TRADE", "Trade-date vs settlement-date difference"
            else:
                raise RuntimeError(f"Unexplained generated break {d.date()} {p} {s} {status}")
            truth.append({"date": d, "portfolio_id": p, "security_id": s,
                          "expected_category": cat, "scenario": scen})

    ibor = pd.DataFrame(ibor_rows)
    cust = pd.DataFrame(cust_rows)

    # ---- data-quality ground truth ---------------------------------------------------
    dq_truth = [
        {"date": days[SCN["missing_price_day"]], "rule": "MISSING_PRICE", "key": missing_px_sec},
        {"date": days[SCN["bad_price_day"]], "rule": "PRICE_JUMP", "key": bad_px_sec},
    ]
    # A stale run is flagged once the same price has appeared STALE_PRICE_DAYS days in a row
    for d_idx in range(SCN["stale_from_day"] + C.STALE_PRICE_DAYS - 1, SCN["stale_to_day"] + 1):
        dq_truth.append({"date": days[d_idx], "rule": "STALE_PRICE", "key": stale_sec})
    # The genuine split also moves the price > 20% vs the trailing 5-day median until
    # post-split prices dominate that window (split day + 2 following days)
    for d_idx in range(SCN["split_day"], SCN["split_day"] + 3):
        dq_truth.append({"date": days[d_idx], "rule": "PRICE_JUMP", "key": split_sec})

    # ---- write custodian files in the custodian's own format ------------------------
    acct = {a[0]: a[2] for a in ACCOUNTS}
    dup_day, bad_isin_day = days[SCN["duplicate_row_day"]], days[SCN["invalid_isin_day"]]
    for d, g in cust.groupby("date"):
        g = g.merge(master[["security_id", "isin", "name", "currency"]], on="security_id")
        g = g.sort_values(["portfolio_id", "isin"]).reset_index(drop=True)
        out = pd.DataFrame({
            "Statement Date": d.strftime("%d/%m/%Y"),
            "Account No": g.portfolio_id.map(acct),
            "ISIN": g["isin"],
            "Security Description": g["name"].str.upper(),
            "Settled Quantity": g.qty.map(lambda v: f"{v:,.2f}"),
            "Local Ccy": g.currency,
            "Local Price": g.price.map(lambda v: f"{v:.4f}"),
            "Local Market Value": g.mv_local.map(lambda v: f"{v:,.2f}"),
            "FX Rate": g.fx.map(lambda v: f"{v:.6f}"),
            "Base Market Value (CAD)": g.mv_cad.map(lambda v: f"{v:,.2f}"),
        })
        if d == dup_day:
            r = int(rng.integers(0, len(out)))
            out = pd.concat([out.iloc[: r + 1], out.iloc[[r]], out.iloc[r + 1:]], ignore_index=True)
            dup_row = g.iloc[r]
            dq_truth.append({"date": d, "rule": "DUPLICATE_RECORD",
                             "key": f"{acct[dup_row['portfolio_id']]}|{dup_row['isin']}"})
        if d == bad_isin_day:
            bonds = g.index[g.security_id.isin(master.loc[master.asset_class == "Bond", "security_id"])
                            & ~g.security_id.isin(used)]
            r = int(bonds[0])
            good = out.at[r, "ISIN"]
            bad = good[:-1] + str((int(good[-1]) + 3) % 10)
            out.at[r, "ISIN"] = bad
            dq_truth.append({"date": d, "rule": "INVALID_ISIN", "key": bad})
        out.to_csv(out_dir / "custodian" / f"custodian_positions_{d:%Y%m%d}.csv", index=False)

    # ---- write the remaining files ---------------------------------------------------
    master.drop(columns="start_price").to_csv(out_dir / "security_master.csv", index=False)
    pd.DataFrame(ACCOUNTS, columns=["portfolio_id", "portfolio_name", "custodian_account"]) \
        .to_csv(out_dir / "accounts.csv", index=False)
    vendor_long.assign(price_date=vendor_long.price_date.dt.strftime("%Y-%m-%d")) \
        .to_csv(out_dir / "vendor_prices.csv", index=False)
    fx_long = fx.stack().rename("cad_per_unit").reset_index()
    fx_long.columns = ["rate_date", "currency", "cad_per_unit"]
    fx_long.assign(rate_date=fx_long.rate_date.dt.strftime("%Y-%m-%d")).to_csv(out_dir / "fx_rates.csv", index=False)
    tr_out = tr.drop(columns=["signed_qty", "cust_effective_date"]).copy()
    for col in ("trade_date", "settle_date"):
        tr_out[col] = tr_out[col].dt.strftime("%Y-%m-%d")
    tr_out.to_csv(out_dir / "ibor_trades.csv", index=False)
    ibor.assign(as_of_date=ibor.as_of_date.dt.strftime("%Y-%m-%d")).to_csv(out_dir / "ibor_positions.csv", index=False)
    truth_df = pd.DataFrame(truth)
    truth_df.assign(date=truth_df.date.dt.strftime("%Y-%m-%d")).to_csv(out_dir / "ground_truth_breaks.csv", index=False)
    dq_df = pd.DataFrame(dq_truth)
    dq_df.assign(date=pd.to_datetime(dq_df.date).dt.strftime("%Y-%m-%d")).to_csv(out_dir / "ground_truth_dq.csv", index=False)

    summary = {
        "seed": seed, "business_days": n_days, "securities": len(master),
        "portfolios": len(portfolios), "trades": len(tr), "ibor_position_rows": len(ibor),
        "custodian_position_rows": len(cust), "expected_breaks": len(truth_df),
        "expected_dq_issues": len(dq_df),
        "scenario_securities": {"split": split_sec, "stale_price": stale_sec,
                                "missing_price": missing_px_sec, "bad_price": bad_px_sec,
                                "unbooked_trade": unbooked_sec, "qty_mismatch": qty_sec,
                                "transfer_in": transfer_sec, "dropped_row": dropped_sec},
    }
    (out_dir / "generation_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    print(json.dumps(generate(), indent=2))
