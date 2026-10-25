"""Extract, clean and load the raw files into SQLite.

The custodian sends files in its own format (DD/MM/YYYY dates, thousands
separators, its own account numbers, upper-case descriptions). This module
normalises that into the internal data model and loads everything into a
relational database.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from . import config as C
from .isin import is_valid_isin

CUSTODIAN_COLUMNS = {
    "Statement Date": "as_of_date",
    "Account No": "custodian_account",
    "ISIN": "isin",
    "Security Description": "description",
    "Settled Quantity": "quantity",
    "Local Ccy": "currency",
    "Local Price": "price",
    "Local Market Value": "market_value_local",
    "FX Rate": "fx_rate",
    "Base Market Value (CAD)": "market_value_cad",
}
NUMERIC = ["quantity", "price", "market_value_local", "fx_rate", "market_value_cad"]


def load_reference(raw_dir: Path = C.RAW_DIR) -> dict[str, pd.DataFrame]:
    raw_dir = Path(raw_dir)
    return {
        "securities": pd.read_csv(raw_dir / "security_master.csv"),
        "accounts": pd.read_csv(raw_dir / "accounts.csv"),
        "vendor_prices": pd.read_csv(raw_dir / "vendor_prices.csv"),
        "fx_rates": pd.read_csv(raw_dir / "fx_rates.csv"),
        "ibor_trades": pd.read_csv(raw_dir / "ibor_trades.csv"),
        "ibor_positions": pd.read_csv(raw_dir / "ibor_positions.csv"),
    }


def _to_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False).str.strip(), errors="coerce")


def load_custodian_raw(raw_dir: Path = C.RAW_DIR) -> pd.DataFrame:
    """Read every custodian file, keeping file name and row number for audit."""
    frames = []
    for path in sorted((Path(raw_dir) / "custodian").glob("custodian_positions_*.csv")):
        df = pd.read_csv(path, dtype=str).rename(columns=CUSTODIAN_COLUMNS)
        df["source_file"] = path.name
        df["source_row"] = range(2, len(df) + 2)  # spreadsheet row numbers (header = row 1)
        frames.append(df)
    raw = pd.concat(frames, ignore_index=True)
    raw["as_of_date"] = pd.to_datetime(raw["as_of_date"], format="%d/%m/%Y").dt.strftime("%Y-%m-%d")
    for col in NUMERIC:
        raw[col] = _to_number(raw[col])
    raw["isin"] = raw["isin"].str.strip().str.upper()
    raw["description"] = raw["description"].str.strip()
    return raw


def normalize_custodian(raw: pd.DataFrame, securities: pd.DataFrame, accounts: pd.DataFrame):
    """Map custodian identifiers to internal ones and remove exact duplicates.

    Returns (clean positions, remap log). A row whose ISIN is unknown or invalid
    is matched on security description; anything still unmatched is dropped and
    surfaces as a data-quality issue rather than silently disappearing.
    """
    df = raw.copy()
    acct_map = accounts.set_index("custodian_account")["portfolio_id"]
    df["portfolio_id"] = df["custodian_account"].map(acct_map)

    isin_map = securities.set_index("isin")["security_id"]
    desc_map = securities.assign(key=securities["name"].str.upper()).set_index("key")["security_id"]
    df["security_id"] = df["isin"].map(isin_map)
    needs_remap = df["security_id"].isna()
    df.loc[needs_remap, "security_id"] = df.loc[needs_remap, "description"].str.upper().map(desc_map)
    remap_log = df.loc[needs_remap, ["as_of_date", "custodian_account", "isin", "description", "security_id",
                                     "source_file", "source_row"]].copy()
    remap_log["isin_valid"] = remap_log["isin"].map(is_valid_isin)

    df = df.dropna(subset=["portfolio_id", "security_id"])
    df = df.drop_duplicates(subset=["as_of_date", "portfolio_id", "security_id", "quantity", "price",
                                    "market_value_local"], keep="first")
    cols = ["as_of_date", "portfolio_id", "security_id", "quantity", "price", "currency", "fx_rate",
            "market_value_local", "market_value_cad", "source_file", "source_row"]
    return df[cols].reset_index(drop=True), remap_log.reset_index(drop=True)


def build_database(db_path: Path, tables: dict[str, pd.DataFrame]) -> sqlite3.Connection:
    """Create the schema and bulk-load each DataFrame into its table."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((C.SQL_DIR / "schema.sql").read_text())
    order = ["securities", "accounts", "vendor_prices", "fx_rates", "ibor_trades", "ibor_positions",
             "custodian_positions"]
    for name in order:
        if name in tables:
            tables[name].to_sql(name, con, if_exists="append", index=False)
    con.commit()
    return con
