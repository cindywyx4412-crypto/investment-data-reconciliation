"""Command-line entry point.

    python run_pipeline.py                 # generate data (if missing), reconcile, build reports
    python run_pipeline.py --regenerate    # rebuild the synthetic data first
    python run_pipeline.py --llm           # also ask Claude to write explanations (needs ANTHROPIC_API_KEY)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from recon import config as C
from recon.dashboard import build_dashboard
from recon.generate_data import generate
from recon.pipeline import run
from recon.report_excel import build_workbook


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Investment data reconciliation & quality pipeline")
    ap.add_argument("--regenerate", action="store_true", help="regenerate synthetic input data")
    ap.add_argument("--seed", type=int, default=C.SEED, help="random seed for data generation")
    ap.add_argument("--llm", action="store_true", help="use Claude for explanations (verified, with fallback)")
    args = ap.parse_args(argv)

    if args.regenerate or not (C.RAW_DIR / "security_master.csv").exists():
        print("Generating synthetic data ...")
        generate(C.RAW_DIR, seed=args.seed)

    llm = None
    if args.llm:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("--llm needs ANTHROPIC_API_KEY; continuing with template explanations.", file=sys.stderr)
        else:
            from recon.explain import AnthropicClient
            llm = AnthropicClient()

    rr = run(C.RAW_DIR, C.DB_PATH, llm=llm)
    m = rr.metrics
    period = m["period_end"][:7]
    xlsx = build_workbook(rr, m, C.OUTPUT_DIR / f"reconciliation_report_{period}.xlsx")
    html = build_dashboard(rr, m, C.OUTPUT_DIR / "dashboard.html")
    (C.OUTPUT_DIR / "metrics.json").write_text(json.dumps(m, indent=2, default=str))

    ev = m["evaluation"]
    print(f"\nPeriod                 {m['period_start']} to {m['period_end']} ({m['business_days']} business days)")
    print(f"Position lines         {m['position_lines_reconciled']:,}")
    print(f"Auto-matched           {m['lines_matched']:,} ({m['auto_match_rate']:.1%})")
    print(f"Breaks                 {m['breaks_total']} -> {m['breaks_auto_explained_timing']} timing (auto), "
          f"{m['exceptions_for_review']} for review")
    print(f"Data-quality issues    {m['dq_issues']}")
    print(f"Explanations           {m['explanation_sources']}")
    print(f"Vs ground truth        detection recall {ev['break_detection_recall']:.0%}, precision "
          f"{ev['break_detection_precision']:.0%}, root-cause accuracy {ev['classification_accuracy']:.0%}, "
          f"DQ recall {ev['dq_recall']:.0%}, DQ precision {ev['dq_precision']:.0%}")
    print(f"Runtime                {m['runtime_seconds']} s")
    print(f"\nWrote {C.DB_PATH.relative_to(C.ROOT)}, {xlsx.relative_to(C.ROOT)}, {html.relative_to(C.ROOT)}, "
          "output/metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
