"""Plain-English explanations for each break, with an optional LLM and a hard
verification gate.

* Template mode (default): deterministic explanations built only from the
  evidence the classifier collected. Always available, no API key needed.
* LLM mode (--llm): an LLM rewrites the explanation from the same evidence.
  Every number and date in its answer is then checked against the evidence.
  If anything cannot be traced back to a source figure, the LLM text is
  rejected and the template is used instead. The model is never trusted blindly.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Callable

from . import config as C
from .classify import CATEGORY_LABELS

LLMFn = Callable[[str, str], str]  # (system prompt, user prompt) -> text


# --------------------------------------------------------------------------- formatting
def q(v) -> str:
    return f"{v:,.0f}" if v is not None and float(v).is_integer() else f"{v:,.2f}"


def money(v) -> str:
    return f"CAD {v:,.2f}"


def px(v) -> str:
    return f"{v:,.4f}"


# --------------------------------------------------------------------------- templates
def template_explanation(category: str, ev: dict) -> tuple[str, str]:
    """Return (explanation, suggested action) for one break, including any secondary issue."""
    text, action = _primary_explanation(category, ev)
    extra = []
    for issue in ev.get("secondary_issues", []):
        if issue == "PRICE_DIFFERENCE":
            extra.append(f"Separately, prices also differ: IBOR {px(ev['ibor_price'])} vs custodian "
                         f"{px(ev['cust_price'])} ({ev['price_diff_pct']:+.2f}%).")
        elif issue == "FX_RATE_DIFFERENCE":
            extra.append(f"Separately, FX rates also differ: IBOR {ev['ibor_fx']:.4f} vs custodian "
                         f"{ev['cust_fx']:.4f}.")
    if extra:
        text = text + " " + " ".join(extra)
        action = action.replace("No action: expected", "Review the secondary issue; the quantity part is expected")
        action += " Investigate the secondary difference before sign-off."
    return text, action


def _primary_explanation(category: str, ev: dict) -> tuple[str, str]:
    who = f"{ev['ticker']} ({ev['name']}) in {ev['portfolio']}"
    if category == "TIMING_UNSETTLED_TRADE":
        t = ev["pending_trades"]
        settle = max(x["settle_date"] for x in t)
        trade_txt = "; ".join(f"{x['side']} {q(x['quantity'])} ({x['trade_id']})" for x in t)
        return (f"IBOR holds {q(ev['ibor_qty'] or 0)} and the custodian {q(ev['cust_qty'] or 0)} units of {who}. "
                f"The difference of {q(ev['qty_diff'])} equals unsettled trade(s) {trade_txt}, which settle on {settle}.",
                f"No action: expected to clear when the trade settles on {settle}. Re-check in that day's run.")
    if category == "TRADE_NOT_BOOKED_AT_CUSTODIAN":
        t = ev["matched_trade"]
        return (f"IBOR holds {q(ev['ibor_qty'] or 0)} and the custodian {q(ev['cust_qty'] or 0)} units of {who}. "
                f"The difference of {q(ev['qty_diff'])} exactly matches trade {t['trade_id']} ({t['side']} "
                f"{q(t['quantity'])}), which should have settled on {t['settle_date']} but is not reflected at the custodian.",
                f"Send trade {t['trade_id']} to the custodian for booking and confirm its settlement status.")
    if category == "CORPORATE_ACTION_UNPROCESSED":
        r = ev["custodian_to_ibor_ratio"]
        ratio_txt = f"{r:g}-for-1" if r >= 1 else f"1-for-{1 / r:g}"
        flags = " A price-jump data-quality flag was raised for the same date." if ev.get("dq_flags") else ""
        return (f"The custodian holds {q(ev['cust_qty'])} units of {who} versus {q(ev['ibor_qty'])} in IBOR, "
                f"a ratio of exactly {r:g} while both sides use the same price ({px(ev['cust_price'])}). "
                f"This pattern indicates a {ratio_txt} split that IBOR has not processed; IBOR market value is "
                f"understated by {money(abs(ev['impact_cad']))}.{flags}",
                f"Ask Corporate Actions to process the {ratio_txt} split in IBOR and confirm the ex-date.")
    if category == "PRICE_DIFFERENCE":
        hint = ""
        flags = ev.get("dq_flags") or []
        if any(f.startswith("STALE_PRICE") for f in flags):
            hint = " The vendor price has been unchanged for several days (stale)."
        elif any(f.startswith("PRICE_JUMP") for f in flags):
            hint = " The vendor price jumped sharply versus its recent median, which suggests a bad print."
        elif any(f.startswith("MISSING_PRICE") for f in flags):
            hint = " No vendor price was received, so IBOR carried the previous price forward."
        return (f"Quantities agree for {who}, but IBOR prices it at {px(ev['ibor_price'])} and the custodian at "
                f"{px(ev['cust_price'])} ({ev['price_diff_pct']:+.2f}%), a market-value difference of "
                f"{money(ev['mv_cad_diff'])}.{hint}",
                "Challenge the vendor price against the custodian and a secondary source; apply a price override "
                "if confirmed and log the exception.")
    if category == "FX_RATE_DIFFERENCE":
        return (f"Quantity and local market value agree for {who}, but IBOR converts {ev['currency']} at "
                f"{ev['ibor_fx']:.4f} and the custodian at {ev['cust_fx']:.4f}, a market-value difference of "
                f"{money(ev['mv_cad_diff'])}.",
                "Confirm the FX snapshot time with the custodian; no position correction is needed if only the "
                "rate source differs.")
    if category == "POSITION_NOT_IN_IBOR":
        return (f"The custodian reports {q(ev['cust_qty'])} units of {who} (worth {money(ev['cust_mv_cad'])}) "
                "with no matching IBOR position and no pending trade to explain it.",
                "Obtain the custodian receipt details (e.g. a free-of-payment transfer-in or corporate action) "
                "and book the position in IBOR once confirmed.")
    if category == "POSITION_NOT_AT_CUSTODIAN":
        return (f"IBOR holds {q(ev['ibor_qty'])} units of {who} (worth {money(ev['ibor_mv_cad'])}) but the "
                "position is absent from the custodian file and no pending trade explains it.",
                "Request a position confirmation from the custodian and check whether the file was incomplete.")
    return (f"IBOR holds {q(ev['ibor_qty'])} and the custodian {q(ev['cust_qty'])} units of {who}; the "
            f"difference of {q(ev['qty_diff'])} does not match any pending trade, recent settled trade or "
            "corporate-action ratio.",
            "Escalate: compare the custodian transaction history with the IBOR trade blotter line by line.")


# --------------------------------------------------------------------------- verification
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUM_RE = re.compile(r"(?<![A-Za-z0-9.])[-+]?\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9])")


def _facts(obj, nums: set[float], strings: set[str]) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            _facts(v, nums, strings)
    elif isinstance(obj, list):
        for v in obj:
            _facts(v, nums, strings)
    elif isinstance(obj, bool) or obj is None:
        return
    elif isinstance(obj, (int, float)):
        nums.add(float(obj))
    elif isinstance(obj, str):
        strings.add(obj)


def verify_text(text: str, evidence: dict) -> tuple[bool, list[str]]:
    """Check that every date and number in `text` is supported by `evidence`.

    A number is supported if it equals a source figure (or its absolute value)
    rounded to the number of decimals shown. Integers up to 10 are allowed for
    phrasing such as '2-for-1' or 'T+1'. Returns (passed, unsupported tokens).
    """
    nums: set[float] = set()
    strings: set[str] = set()
    _facts(evidence, nums, strings)
    nums |= {abs(n) for n in nums}

    unsupported = []
    scrubbed = text
    for s in sorted(strings, key=len, reverse=True):  # remove IDs / tickers before reading numbers
        if len(s) >= 3 and not _DATE_RE.fullmatch(s):
            scrubbed = scrubbed.replace(s, " ")
    for d in _DATE_RE.findall(scrubbed):
        if d not in strings:
            unsupported.append(d)
    scrubbed = _DATE_RE.sub(" ", scrubbed)
    for tok in _NUM_RE.findall(scrubbed):
        clean = tok.replace(",", "").lstrip("+")
        try:
            val = float(clean)
        except ValueError:
            continue
        decimals = len(clean.split(".")[1]) if "." in clean else 0
        tol = 0.5 * 10 ** (-decimals) + 1e-9
        if abs(val) <= 10 and float(val).is_integer():
            continue
        if not any(abs(abs(val) - n) <= tol for n in nums):
            unsupported.append(tok)
    return (not unsupported), unsupported


# --------------------------------------------------------------------------- LLM client
SYSTEM_PROMPT = (
    "You are an investment operations analyst. Explain one position reconciliation break to an "
    "operations reviewer in at most two sentences. Use ONLY the facts in the JSON you are given: "
    "do not invent or recompute numbers, dates, trades or causes, and copy figures exactly as they "
    "appear. Do not recommend an action."
)


class AnthropicClient:
    """Minimal Claude Messages API client using only the standard library."""

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: int = 30):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.model = model or os.environ.get("RECON_LLM_MODEL", C.LLM_DEFAULT_MODEL)
        self.timeout = timeout

    def __call__(self, system: str, user: str) -> str:
        body = json.dumps({
            "model": self.model, "max_tokens": 300, "system": system,
            "messages": [{"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body, method="POST",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.load(resp)
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()


def build_user_prompt(category: str, evidence: dict) -> str:
    return (f"Break category: {CATEGORY_LABELS[category]}\n"
            f"Facts (JSON):\n{json.dumps(evidence, indent=2, default=str)}")


# --------------------------------------------------------------------------- main entry
def explain_breaks(breaks, llm: LLMFn | None = None, max_llm_calls: int = C.LLM_MAX_CALLS):
    """Add explanation, explanation_src and suggested_action columns to breaks."""
    out = breaks.copy()
    explanations, sources, actions = [], [], []
    calls = 0
    for r in out.itertuples():
        ev = r.evidence
        text, action = template_explanation(r.category, ev)
        src = "TEMPLATE"
        if llm is not None and r.needs_review and calls < max_llm_calls:
            calls += 1
            try:
                candidate = llm(SYSTEM_PROMPT, build_user_prompt(r.category, ev)).strip()
                ok, bad = verify_text(candidate, ev)
                if ok and candidate:
                    text, src = candidate, "LLM_VERIFIED"
                else:
                    src = "LLM_REJECTED"
                    ev["llm_rejected_tokens"] = bad
            except Exception as exc:  # network, auth, rate limit ... -> template
                src = "TEMPLATE_LLM_ERROR"
                ev["llm_error"] = type(exc).__name__
        explanations.append(text)
        sources.append(src)
        actions.append(action)
    out["explanation"] = explanations
    out["explanation_src"] = sources
    out["suggested_action"] = actions
    return out
