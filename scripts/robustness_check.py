"""Re-run generation + pipeline on many random seeds to check results are not a lucky draw.

    python scripts/robustness_check.py --seeds 20
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recon.generate_data import generate  # noqa: E402
from recon.pipeline import run  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--out", default="docs/robustness.json")
    args = ap.parse_args()

    rows = []
    for seed in range(1, args.seeds + 1):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            generate(tmp / "raw", seed=seed)
            rr = run(tmp / "raw", tmp / "recon.db")
            ev, m = rr.metrics["evaluation"], rr.metrics
            rows.append({
                "seed": seed, "lines": m["position_lines_reconciled"], "breaks_expected": ev["breaks_expected"],
                "breaks_missed": ev["break_missed"], "false_breaks": ev["break_false_positives"],
                "classified_correctly": ev["classification_correct"],
                "dq_expected": ev["dq_expected"], "dq_flagged": ev["dq_flagged"],
                "dq_missed": ev["dq_missed"], "dq_false_positives": ev["dq_false_positives"],
                "dq_false_positive_detail": ev["unexpected_dq"],
            })
            print(f"seed {seed:>2}: {ev['breaks_expected']} breaks, missed {ev['break_missed']}, "
                  f"misclassified {ev['breaks_expected'] - ev['classification_correct']}, "
                  f"DQ missed {ev['dq_missed']}, DQ false positives {ev['dq_false_positives']}")

    tot = lambda k: sum(r[k] for r in rows)
    summary = {
        "seeds": args.seeds,
        "position_lines": tot("lines"),
        "breaks_expected": tot("breaks_expected"),
        "breaks_missed": tot("breaks_missed"),
        "false_breaks": tot("false_breaks"),
        "classification_accuracy": round(tot("classified_correctly") / tot("breaks_expected"), 4),
        "dq_expected": tot("dq_expected"),
        "dq_missed": tot("dq_missed"),
        "dq_false_positives": tot("dq_false_positives"),
        "dq_precision": round((tot("dq_flagged") - tot("dq_false_positives")) / tot("dq_flagged"), 4),
        "runs": rows,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "runs"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
