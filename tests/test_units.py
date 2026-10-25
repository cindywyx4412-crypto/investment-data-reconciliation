"""Unit tests for the individual rules. Run: python -m unittest discover -s tests -v"""
import unittest

import pandas as pd

from recon import config as C
from recon.classify import classify_breaks
from recon.explain import explain_breaks, template_explanation, verify_text
from recon.isin import is_valid_isin, isin_check_digit, make_isin
from recon.quality import check_duplicates, check_price_jumps, check_stale_prices
from recon.tolerance import line_status

SECURITIES = pd.DataFrame([
    {"security_id": "S1", "isin": make_isin("CA", "123456789"), "ticker": "AAA", "name": "Alpha Inc.",
     "asset_class": "Equity", "currency": "CAD", "multiplier": 1.0},
    {"security_id": "S2", "isin": make_isin("US", "987654321"), "ticker": "BBB", "name": "Beta Inc.",
     "asset_class": "Equity", "currency": "USD", "multiplier": 1.0},
])
NO_DQ = pd.DataFrame(columns=["as_of_date", "source", "rule", "severity", "record_key", "security_id", "detail"])
NO_TRADES = pd.DataFrame(columns=["trade_id", "portfolio_id", "security_id", "side", "quantity", "price",
                                  "trade_date", "settle_date"])


def line(iq, cq, ip=10.0, cp=10.0, ifx=1.0, cfx=1.0, sid="S1", d="2026-09-10"):
    i_mv = None if iq is None else iq * ip
    c_mv = None if cq is None else cq * cp
    row = {"as_of_date": d, "portfolio_id": "P1", "security_id": sid,
           "ibor_qty": iq, "cust_qty": cq, "ibor_price": ip if iq is not None else None,
           "cust_price": cp if cq is not None else None, "ibor_mv_local": i_mv, "cust_mv_local": c_mv,
           "ibor_fx": ifx if iq is not None else None, "cust_fx": cfx if cq is not None else None,
           "ibor_mv_cad": None if i_mv is None else i_mv * ifx, "cust_mv_cad": None if c_mv is None else c_mv * cfx}
    row["qty_diff"] = (iq or 0) - (cq or 0)
    row["mv_cad_diff"] = (row["ibor_mv_cad"] or 0) - (row["cust_mv_cad"] or 0)
    row["status"] = line_status(iq, cq, row["ibor_price"], row["cust_price"], row["ibor_mv_cad"], row["cust_mv_cad"])
    return row


def trade(side, qty, trade_date, settle_date, sid="S1", tid="T1"):
    return {"trade_id": tid, "portfolio_id": "P1", "security_id": sid, "side": side, "quantity": qty,
            "price": 10.0, "trade_date": trade_date, "settle_date": settle_date}


def classify(rows, trades=None):
    tr = pd.DataFrame(trades) if trades else NO_TRADES
    return classify_breaks(pd.DataFrame(rows), tr, SECURITIES, NO_DQ)


class TestIsin(unittest.TestCase):
    def test_known_public_isins(self):
        for isin in ("US0378331005", "US5949181045", "US02079K3059"):
            self.assertTrue(is_valid_isin(isin), isin)

    def test_wrong_check_digit_and_format(self):
        self.assertFalse(is_valid_isin("US0378331006"))
        self.assertFalse(is_valid_isin("US037833100"))
        self.assertFalse(is_valid_isin(None))

    def test_make_isin_round_trip(self):
        isin = make_isin("CA", "000000001")
        self.assertTrue(is_valid_isin(isin))
        self.assertEqual(isin[-1], isin_check_digit(isin[:11]))


class TestTolerance(unittest.TestCase):
    def test_statuses(self):
        self.assertEqual(line_status(100, 100, 10, 10, 1000, 1000), "MATCHED")
        self.assertEqual(line_status(100, None, 10, None, 1000, None), "MISSING_AT_CUSTODIAN")
        self.assertEqual(line_status(None, 100, None, 10, None, 1000), "MISSING_IN_IBOR")
        self.assertEqual(line_status(100, 101, 10, 10, 1000, 1010), "QTY_BREAK")
        self.assertEqual(line_status(100, 100, 10.1, 10, 1010, 1000), "PRICE_BREAK")
        # price within 25 bps, but CAD 1,000 on a 1m position breaches 1 bp / CAD 5
        self.assertEqual(line_status(1e5, 1e5, 10.01, 10, 1.001e6, 1e6), "MV_BREAK")
        # 20 bps price gap and a CAD 2 value gap are both inside tolerance
        self.assertEqual(line_status(100, 100, 10.02, 10, 1002, 1000), "MATCHED")


class TestQualityRules(unittest.TestCase):
    def test_stale_price_needs_three_days(self):
        v = pd.DataFrame({"price_date": [f"2026-09-0{i}" for i in range(1, 6)], "security_id": "S1",
                          "price": [10.0, 11.0, 11.0, 11.0, 12.0]})
        out = check_stale_prices(v)
        self.assertEqual(out["as_of_date"].tolist(), ["2026-09-04"])

    def test_price_jump_flags_bad_print_once(self):
        prices = [10.0, 10.1, 10.0, 10.2, 10.1, 1010.0, 10.2, 10.1]
        v = pd.DataFrame({"price_date": [f"2026-09-{i + 1:02d}" for i in range(8)], "security_id": "S1",
                          "price": prices})
        out = check_price_jumps(v)
        self.assertEqual(out["as_of_date"].tolist(), ["2026-09-06"])

    def test_duplicates(self):
        raw = pd.DataFrame({"as_of_date": ["d"] * 3, "custodian_account": ["A"] * 3,
                            "isin": ["X", "X", "Y"], "source_file": "f", "source_row": [2, 3, 4]})
        out = check_duplicates(raw)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["record_key"], "A|X")


class TestClassifier(unittest.TestCase):
    def test_timing_is_auto_cleared(self):
        b = classify([line(150, 100)], [trade("BUY", 50, "2026-09-10", "2026-09-11")])
        self.assertEqual(b.iloc[0]["category"], "TIMING_UNSETTLED_TRADE")
        self.assertEqual(b.iloc[0]["needs_review"], 0)

    def test_new_position_bought_today_is_timing(self):
        b = classify([line(50, None)], [trade("BUY", 50, "2026-09-10", "2026-09-11")])
        self.assertEqual(b.iloc[0]["category"], "TIMING_UNSETTLED_TRADE")

    def test_settled_trade_not_booked(self):
        b = classify([line(150, 100)], [trade("BUY", 50, "2026-09-08", "2026-09-09")])
        self.assertEqual(b.iloc[0]["category"], "TRADE_NOT_BOOKED_AT_CUSTODIAN")

    def test_split(self):
        b = classify([line(100, 200, ip=5.0, cp=5.0)])
        self.assertEqual(b.iloc[0]["category"], "CORPORATE_ACTION_UNPROCESSED")

    def test_price_vs_fx(self):
        b = classify([line(1000, 1000, ip=10.5, cp=10.0, sid="S1"),
                      line(1000, 1000, ifx=1.36, cfx=1.37, sid="S2")])
        cats = dict(zip(b["security_id"], b["category"]))
        self.assertEqual(cats, {"S1": "PRICE_DIFFERENCE", "S2": "FX_RATE_DIFFERENCE"})

    def test_missing_sides_and_unexplained(self):
        b = classify([line(None, 100, sid="S1"), line(100, None, sid="S2")])
        cats = dict(zip(b["security_id"], b["category"]))
        self.assertEqual(cats, {"S1": "POSITION_NOT_IN_IBOR", "S2": "POSITION_NOT_AT_CUSTODIAN"})
        b = classify([line(100, 137)])
        self.assertEqual(b.iloc[0]["category"], "UNEXPLAINED_QUANTITY")

    def test_secondary_issue_blocks_auto_clear(self):
        b = classify([line(150, 100, ip=10.5, cp=10.0)], [trade("BUY", 50, "2026-09-10", "2026-09-11")])
        self.assertEqual(b.iloc[0]["category"], "TIMING_UNSETTLED_TRADE")
        self.assertEqual(b.iloc[0]["needs_review"], 1)
        self.assertEqual(b.iloc[0]["evidence"]["secondary_issues"], ["PRICE_DIFFERENCE"])

    def test_age_counts_consecutive_days(self):
        rows = [line(100, 137, d="2026-09-09"), line(100, 137, d="2026-09-10")]
        b = classify(rows)
        self.assertEqual(b["age_days"].tolist(), [1, 2])


class TestExplanationVerification(unittest.TestCase):
    def setUp(self):
        self.breaks = classify([line(1000, 1000, ip=10.5, cp=10.0)])
        self.ev = self.breaks.iloc[0]["evidence"]

    def test_template_passes_its_own_check(self):
        text, _ = template_explanation("PRICE_DIFFERENCE", self.ev)
        self.assertTrue(verify_text(text, self.ev)[0], text)

    def test_hallucinated_number_is_rejected(self):
        ok, bad = verify_text("IBOR prices AAA at 10.5000 versus 10.0000, a loss of CAD 7,777.00.", self.ev)
        self.assertFalse(ok)
        self.assertIn("7,777.00", bad)

    def test_hallucinated_date_is_rejected(self):
        ok, bad = verify_text("The price was last updated on 2026-08-15.", self.ev)
        self.assertFalse(ok)

    def test_llm_good_answer_used(self):
        good = lambda system, user: "AAA is priced at 10.5000 in IBOR but 10.0000 at the custodian (+5.00%)."
        out = explain_breaks(self.breaks, llm=good)
        self.assertEqual(out.iloc[0]["explanation_src"], "LLM_VERIFIED")

    def test_llm_bad_answer_falls_back_to_template(self):
        bad = lambda system, user: "AAA is overpriced by CAD 123,456.00."
        out = explain_breaks(self.breaks, llm=bad)
        self.assertEqual(out.iloc[0]["explanation_src"], "LLM_REJECTED")
        self.assertIn("Quantities agree", out.iloc[0]["explanation"])

    def test_llm_error_falls_back_to_template(self):
        def broken(system, user):
            raise TimeoutError
        out = explain_breaks(self.breaks, llm=broken)
        self.assertEqual(out.iloc[0]["explanation_src"], "TEMPLATE_LLM_ERROR")


if __name__ == "__main__":
    unittest.main()
