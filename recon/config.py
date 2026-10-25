"""Central configuration: paths, tolerances and data-quality thresholds.

Every threshold used anywhere in the pipeline lives here, so a reviewer can
see (and change) the business rules in one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
OUTPUT_DIR = ROOT / "output"
SQL_DIR = ROOT / "sql"
DB_PATH = OUTPUT_DIR / "recon.db"

# Simulation window: one month of business days (Labour Day excluded).
SIM_START = "2026-09-01"
SIM_END = "2026-09-30"
HOLIDAYS = ["2026-09-07"]
SEED = 20260930
SETTLEMENT_LAG_DAYS = 1  # T+1, configurable


@dataclass(frozen=True)
class Tolerances:
    """Thresholds that decide whether two records 'match'."""

    quantity_abs: float = 0.0001      # units / face value
    price_rel: float = 0.0025         # 25 bps relative price difference
    mv_cad_abs: float = 5.00          # CAD absolute market-value difference
    mv_cad_rel: float = 0.0001        # 1 bp of custodian market value
    local_mv_rel: float = 0.0001      # used to separate FX issues from price issues


TOL = Tolerances()

# Data-quality rule parameters
STALE_PRICE_DAYS = 3          # same vendor price for >= 3 consecutive business days
PRICE_JUMP_THRESHOLD = 0.20   # > 20% away from the median of the prior 5 prices
PRICE_JUMP_LOOKBACK = 5
MV_CONSISTENCY_REL = 0.0005   # quantity x price x multiplier vs reported market value

# Corporate-action ratios the classifier will recognise (custodian qty / IBOR qty)
SPLIT_RATIOS = (2.0, 3.0, 4.0, 5.0, 10.0, 0.5, 1 / 3, 0.25, 0.2, 0.1)

# Severity bands on absolute CAD market-value impact
SEVERITY_BANDS = [
    (1_000_000, "Critical"),
    (100_000, "High"),
    (10_000, "Medium"),
    (0, "Low"),
]

# LLM settings (only used when --llm is passed and ANTHROPIC_API_KEY is set)
LLM_DEFAULT_MODEL = "claude-haiku-5-5"
LLM_MAX_CALLS = 60
