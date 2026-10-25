"""Run the SQL position reconciliation and store the results."""
from __future__ import annotations

import sqlite3

import pandas as pd

from . import config as C


def reconcile_positions(con: sqlite3.Connection) -> pd.DataFrame:
    sql = (C.SQL_DIR / "reconcile_positions.sql").read_text()
    params = {
        "qty_tol": C.TOL.quantity_abs,
        "price_rel_tol": C.TOL.price_rel,
        "mv_abs_tol": C.TOL.mv_cad_abs,
        "mv_rel_tol": C.TOL.mv_cad_rel,
    }
    results = pd.read_sql_query(sql, con, params=params)
    con.execute("DELETE FROM recon_results")
    results.to_sql("recon_results", con, if_exists="append", index=False)
    con.commit()
    return results
