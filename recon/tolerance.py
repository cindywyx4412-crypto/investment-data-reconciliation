"""Single Python definition of 'is this position line a break?'.

The SQL in sql/reconcile_positions.sql implements the same rule set-based;
tests/test_reconcile.py asserts the two always agree.
"""
from __future__ import annotations

import math

from .config import TOL


def _missing(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def line_status(ibor_qty, cust_qty, ibor_price, cust_price, ibor_mv_cad, cust_mv_cad) -> str:
    """Return MATCHED or the first failing check for one position line."""
    if _missing(cust_qty):
        return "MISSING_AT_CUSTODIAN"
    if _missing(ibor_qty):
        return "MISSING_IN_IBOR"
    if abs(ibor_qty - cust_qty) > TOL.quantity_abs:
        return "QTY_BREAK"
    if cust_price and abs(ibor_price - cust_price) / abs(cust_price) > TOL.price_rel:
        return "PRICE_BREAK"
    mv_tol = max(TOL.mv_cad_abs, TOL.mv_cad_rel * abs(cust_mv_cad))
    if abs(ibor_mv_cad - cust_mv_cad) > mv_tol:
        return "MV_BREAK"
    return "MATCHED"
