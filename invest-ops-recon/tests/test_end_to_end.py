"""End-to-end tests on freshly generated data (temporary folder)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from recon.generate_data import generate
from recon.pipeline import run
from recon.tolerance import line_status


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.summary = generate(cls.tmp / "raw")
        cls.rr = run(cls.tmp / "raw", cls.tmp / "recon.db")

    def test_every_injected_break_detected_and_classified(self):
        ev = self.rr.metrics["evaluation"]
        self.assertEqual(ev["break_missed"], 0, ev["missed_breaks"])
        self.assertEqual(ev["break_false_positives"], 0, ev["unexpected_breaks"])
        self.assertEqual(ev["classification_accuracy"], 1.0, ev["confusion"])

    def test_every_injected_dq_issue_flagged(self):
        ev = self.rr.metrics["evaluation"]
        self.assertEqual(ev["dq_missed"], 0, ev["missed_dq"])

    def test_sql_and_python_rules_agree(self):
        for r in self.rr.results.itertuples():
            py = line_status(r.ibor_qty, r.cust_qty, r.ibor_price, r.cust_price, r.ibor_mv_cad, r.cust_mv_cad)
            self.assertEqual(py, r.status, r)

    def test_duplicate_removed_and_bad_isin_remapped(self):
        m = self.rr.metrics
        self.assertEqual(m["custodian_rows_received"] - m["custodian_rows_loaded"], 1)
        self.assertEqual(m["identifiers_remapped"], 1)

    def test_database_is_queryable(self):
        con = sqlite3.connect(self.tmp / "recon.db")
        n = con.execute("SELECT COUNT(*) FROM breaks WHERE needs_review = 1").fetchone()[0]
        con.close()
        self.assertEqual(n, self.rr.metrics["exceptions_for_review"])

    def test_reproducible(self):
        again = generate(self.tmp / "raw2")
        self.assertEqual(again, self.summary)


if __name__ == "__main__":
    unittest.main()
