"""Score the pipeline against the generator's ground truth.

Because every injected problem is recorded when the data is generated, the
pipeline's accuracy can be measured rather than claimed.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config as C


def evaluate(breaks: pd.DataFrame, dq: pd.DataFrame, raw_dir: Path = C.RAW_DIR) -> dict:
    truth = pd.read_csv(Path(raw_dir) / "ground_truth_breaks.csv")
    dq_truth = pd.read_csv(Path(raw_dir) / "ground_truth_dq.csv")

    key = ["as_of_date", "portfolio_id", "security_id"]
    t = truth.rename(columns={"date": "as_of_date"})
    m = breaks[key + ["category"]].merge(t[key + ["expected_category"]], on=key, how="outer", indicator=True)
    detected_true = int((m["_merge"] == "both").sum())
    false_pos = int((m["_merge"] == "left_only").sum())
    missed = int((m["_merge"] == "right_only").sum())
    both = m[m["_merge"] == "both"]
    correct = int((both["category"] == both["expected_category"]).sum())

    confusion = (both.groupby(["expected_category", "category"]).size()
                 .rename("count").reset_index().sort_values(["expected_category", "category"]))
    per_cat = (both.assign(ok=both["category"] == both["expected_category"])
               .groupby("expected_category")["ok"].agg(["sum", "count"]).reset_index()
               .rename(columns={"sum": "correct", "count": "expected"}))

    dq_found = dq.rename(columns={"as_of_date": "date"})[["date", "rule", "record_key"]] \
        .rename(columns={"record_key": "key"}).drop_duplicates()
    dm = dq_found.merge(dq_truth, on=["date", "rule", "key"], how="outer", indicator=True)
    dq_tp = int((dm["_merge"] == "both").sum())
    dq_fp = int((dm["_merge"] == "left_only").sum())
    dq_fn = int((dm["_merge"] == "right_only").sum())

    def ratio(a, b):
        return round(a / b, 4) if b else None

    return {
        "breaks_expected": int(len(t)),
        "breaks_detected": int(len(breaks)),
        "break_detection_recall": ratio(detected_true, detected_true + missed),
        "break_detection_precision": ratio(detected_true, detected_true + false_pos),
        "break_false_positives": false_pos,
        "break_missed": missed,
        "classification_accuracy": ratio(correct, len(both)),
        "classification_correct": correct,
        "dq_expected": int(len(dq_truth)),
        "dq_flagged": int(len(dq_found)),
        "dq_recall": ratio(dq_tp, dq_tp + dq_fn),
        "dq_precision": ratio(dq_tp, dq_tp + dq_fp),
        "dq_false_positives": dq_fp,
        "dq_missed": dq_fn,
        "per_category": per_cat.to_dict("records"),
        "confusion": confusion.to_dict("records"),
        "unexpected_breaks": m[m["_merge"] == "left_only"][key + ["category"]].to_dict("records"),
        "missed_breaks": m[m["_merge"] == "right_only"][key + ["expected_category"]].to_dict("records"),
        "unexpected_dq": dm[dm["_merge"] == "left_only"][["date", "rule", "key"]].to_dict("records"),
        "missed_dq": dm[dm["_merge"] == "right_only"][["date", "rule", "key"]].to_dict("records"),
    }
