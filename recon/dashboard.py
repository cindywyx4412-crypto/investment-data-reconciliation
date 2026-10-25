"""Single-file HTML dashboard (no external libraries, works offline and on GitHub Pages).

Charts are inline SVG generated in Python; a few lines of vanilla JS add
hover tooltips and table filters.
"""
from __future__ import annotations

import html
import math
from pathlib import Path

import pandas as pd

from .classify import CATEGORY_LABELS

E = html.escape
SEV_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
SEV_ICON = {"Critical": "&#9650;", "High": "&#9670;", "Medium": "&#9679;", "Low": "&#9675;"}


def _nice_max(v: float) -> int:
    if v <= 5:
        return 5
    step = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * step:
            return int(m * step)
    return int(10 * step)


def line_chart(dates: list[str], series: list[tuple[str, list[int], str]]) -> str:
    W, H, L, R, T, B = 720, 260, 40, 120, 14, 34
    pw, ph = W - L - R, H - T - B
    ymax = _nice_max(max(max(v) for _, v, _ in series))
    n = len(dates)
    x = lambda i: L + (pw * i / (n - 1) if n > 1 else pw / 2)
    y = lambda v: T + ph - ph * v / ymax
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Daily breaks by type" class="chart">']
    for k in range(5):
        v = ymax * k / 4
        out.append(f'<line x1="{L}" x2="{L + pw}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                   f'<text x="{L - 8}" y="{y(v) + 4:.1f}" class="tick" text-anchor="end">{v:g}</text>')
    out.append(f'<line x1="{L}" x2="{L + pw}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="axis"/>')
    for i, d in enumerate(dates):
        if i % 3 == 0 or i == n - 1:
            label = pd.Timestamp(d).strftime("%b %d")
            out.append(f'<text x="{x(i):.1f}" y="{H - 10}" class="tick" text-anchor="middle">{label}</text>')
    for name, vals, var in series:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
        out.append(f'<polyline points="{pts}" fill="none" stroke="var({var})" stroke-width="2" '
                   'stroke-linejoin="round" stroke-linecap="round"/>')
        for i, v in enumerate(vals):
            tip = f"{pd.Timestamp(dates[i]).strftime('%a %b %d')} | {name}: {v}"
            out.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="4" fill="var({var})" class="pt" '
                       f'stroke="var(--surface)" stroke-width="2" data-tip="{E(tip)}"/>')
            out.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="11" fill="transparent" data-tip="{E(tip)}"/>')
        out.append(f'<text x="{x(n - 1) + 10:.1f}" y="{y(vals[-1]) + 4:.1f}" class="dlabel">{E(name)}</text>')
    out.append("</svg>")
    return "".join(out)


def bar_chart(items: list[tuple[str, int, float]]) -> str:
    W, row, L, R = 720, 30, 230, 70
    H = row * len(items) + 12
    vmax = max(v for _, v, _ in items) or 1
    pw = W - L - R
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Exceptions by root cause" class="chart">']
    for i, (label, v, impact) in enumerate(items):
        yy = 6 + i * row
        w = max(2, pw * v / vmax)
        tip = f"{label}: {v} exception{'s' if v != 1 else ''}, gross impact CAD {impact:,.0f}"
        out.append(f'<text x="{L - 10}" y="{yy + 15}" class="blabel" text-anchor="end">{E(label)}</text>'
                   f'<path d="M{L},{yy + 4} h{w - 4} a4,4 0 0 1 4,4 v10 a4,4 0 0 1 -4,4 h{-(w - 4)} z" '
                   f'fill="var(--series-1)" data-tip="{E(tip)}"/>'
                   f'<text x="{L + w + 8}" y="{yy + 17}" class="bvalue">{v}</text>')
    out.append(f'<line x1="{L}" x2="{L}" y1="2" y2="{H - 4}" class="axis"/></svg>')
    return "".join(out)


def build_dashboard(rr, metrics: dict, path: Path) -> Path:
    sec = rr.reference["securities"].set_index("security_id")
    brk = rr.breaks.copy()
    dates = sorted(rr.results["as_of_date"].unique())
    timing = [int(((brk.as_of_date == d) & (brk.needs_review == 0)).sum()) for d in dates]
    review = [int(((brk.as_of_date == d) & (brk.needs_review == 1)).sum()) for d in dates]
    ex = brk[brk.needs_review == 1].copy()
    by_cat = (ex.groupby("category").agg(n=("category", "size"), imp=("impact_cad", lambda s: s.abs().sum()))
              .sort_values("n", ascending=False))
    bars = [(CATEGORY_LABELS[c], int(r.n), float(r.imp)) for c, r in by_cat.iterrows()]
    ev = metrics["evaluation"]

    ex["sev_rank"] = ex.severity.map(SEV_ORDER)
    ex["abs_imp"] = ex.impact_cad.abs()
    ex = ex.sort_values(["sev_rank", "abs_imp"], ascending=[True, False])
    rows = []
    for r in ex.itertuples():
        s = sec.loc[r.security_id]
        rows.append(
            f'<tr data-portfolio="{E(r.portfolio_id)}" data-category="{E(CATEGORY_LABELS[r.category])}" '
            f'data-severity="{E(r.severity)}">'
            f'<td class="nowrap">{E(r.as_of_date)}</td><td>{E(r.portfolio_id)}</td>'
            f'<td><b>{E(s.ticker)}</b><div class="muted">{E(s["name"])}</div></td>'
            f'<td>{E(CATEGORY_LABELS[r.category])}</td>'
            f'<td><span class="sev sev-{r.severity.lower()}">{SEV_ICON[r.severity]} {E(r.severity)}</span></td>'
            f'<td class="num">{r.impact_cad:,.2f}</td><td class="num">{r.age_days}</td>'
            f'<td><details><summary>{E(r.suggested_action)}</summary><p>{E(r.explanation)}</p></details></td></tr>')
    dq_rows = "".join(
        f"<tr><td class='nowrap'>{E(r.as_of_date)}</td><td>{E(r.rule)}</td><td>{E(r.severity)}</td>"
        f"<td>{E(r.record_key)}</td><td>{E(r.detail)}</td></tr>" for r in rr.dq.itertuples())

    def opts(values):
        return "".join(f"<option>{E(v)}</option>" for v in values)

    kpis = [
        ("Position lines reconciled", f"{metrics['position_lines_reconciled']:,}",
         f"{metrics['business_days']} business days, {metrics['portfolios']} portfolios"),
        ("Auto-match rate", f"{metrics['auto_match_rate']:.1%}", "Within quantity, price and value tolerances"),
        ("Breaks explained automatically", f"{metrics['breaks_auto_explained_timing']} of {metrics['breaks_total']}",
         "Unsettled trades, cleared on settlement"),
        ("Exceptions for review", f"{metrics['exceptions_for_review']}",
         f"{metrics['exception_rate_of_lines']:.1%} of all lines"),
        ("Data-quality issues caught", f"{metrics['dq_issues']}", "Before reconciliation"),
        ("Root-cause accuracy", f"{ev['classification_accuracy']:.0%}",
         f"{ev['classification_correct']} of {ev['breaks_expected']} vs ground truth"),
    ]
    kpi_html = "".join(f'<div class="kpi"><div class="kl">{E(a)}</div><div class="kv">{E(b)}</div>'
                       f'<div class="ks">{E(c)}</div></div>' for a, b, c in kpis)

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reconciliation Dashboard</title>
<style>
:root {{ color-scheme: light; --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10); --series-1:#2a78d6; --series-2:#eb6834;
  --crit:#d03b3b; --high:#ec835a; --med:#fab219; --low:#898781; --accent:#1f3a5f; }}
@media (prefers-color-scheme: dark) {{ :root:where(:not([data-theme="light"])) {{ color-scheme: dark; --page:#0d0d0d;
  --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
  --series-1:#3987e5; --series-2:#d95926; --accent:#86b6ef; }} }}
:root[data-theme="dark"] {{ color-scheme: dark; --page:#0d0d0d; --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10); --series-1:#3987e5; --series-2:#d95926; --accent:#86b6ef; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--page); color:var(--ink); font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:1180px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ font-size:24px; margin:0 0 4px; }} h2 {{ font-size:16px; margin:0 0 4px; }}
.sub {{ color:var(--ink2); margin:0 0 20px; }} .tag {{ display:inline-block; padding:1px 8px; border-radius:99px;
  border:1px solid var(--border); color:var(--ink2); font-size:12px; margin-left:6px; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin-bottom:16px; }}
.kpi,.card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:14px 16px; }}
.kl {{ color:var(--ink2); font-size:12px; }} .kv {{ font-size:26px; font-weight:650; margin:2px 0; }}
.ks {{ color:var(--muted); font-size:12px; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; }}
@media (max-width:860px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
.cap {{ color:var(--ink2); font-size:12px; margin:0 0 8px; }}
.legend {{ display:flex; gap:16px; font-size:12px; color:var(--ink2); margin-bottom:4px; }}
.sw {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; vertical-align:-1px; }}
svg.chart {{ width:100%; height:auto; display:block; }}
.grid {{ stroke:var(--grid); stroke-width:1; }} .axis {{ stroke:var(--axis); stroke-width:1; }}
.tick {{ fill:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; }}
.dlabel,.blabel {{ fill:var(--ink2); font-size:12px; }} .bvalue {{ fill:var(--ink); font-size:12px; font-weight:600; }}
.filters {{ display:flex; flex-wrap:wrap; gap:8px; margin:8px 0 12px; align-items:center; }}
select {{ font:inherit; padding:5px 8px; border-radius:8px; border:1px solid var(--border); background:var(--surface); color:var(--ink); }}
.scroll {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ text-align:left; color:var(--ink2); font-weight:600; border-bottom:1px solid var(--axis); padding:8px; white-space:nowrap; }}
td {{ border-bottom:1px solid var(--grid); padding:8px; vertical-align:top; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }} .nowrap {{ white-space:nowrap; }}
.muted {{ color:var(--muted); font-size:12px; }}
details summary {{ cursor:pointer; color:var(--ink); }} details p {{ color:var(--ink2); margin:6px 0 0; }}
.sev {{ white-space:nowrap; font-weight:600; font-size:12px; }}
.sev-critical {{ color:var(--crit); }} .sev-high {{ color:var(--high); }} .sev-medium {{ color:#b07a00; }} .sev-low {{ color:var(--low); }}
:root[data-theme="dark"] .sev-medium {{ color:var(--med); }}
@media (prefers-color-scheme: dark) {{ :root:where(:not([data-theme="light"])) .sev-medium {{ color:var(--med); }} }}
#tip {{ position:fixed; pointer-events:none; background:var(--ink); color:var(--surface); padding:6px 10px;
  border-radius:8px; font-size:12px; opacity:0; transition:opacity .1s; z-index:9; max-width:280px; }}
footer {{ color:var(--muted); font-size:12px; margin-top:20px; }}
</style></head><body><main>
<h1>Investment Operations Reconciliation<span class="tag">synthetic data</span></h1>
<p class="sub">IBOR (trade-date book of record) vs custodian (settled) positions, {E(metrics['period_start'])} to
{E(metrics['period_end'])}. Generated by <code>run_pipeline.py</code>.</p>
<section class="kpis">{kpi_html}</section>
<section class="grid2">
 <div class="card"><h2>Daily breaks</h2><p class="cap">Timing breaks clear on settlement; exceptions need a reviewer.</p>
  <div class="legend"><span><span class="sw" style="background:var(--series-1)"></span>Timing (auto)</span>
  <span><span class="sw" style="background:var(--series-2)"></span>Exceptions</span></div>
  {line_chart(dates, [("Timing (auto)", timing, "--series-1"), ("Exceptions", review, "--series-2")])}</div>
 <div class="card"><h2>Exceptions by root cause</h2><p class="cap">Count of breaks needing review this month.</p>
  {bar_chart(bars)}</div>
</section>
<section class="card"><h2>Exceptions for review</h2>
 <p class="cap">Sorted by severity, then absolute CAD impact. Open a row for the explanation built from the evidence.</p>
 <div class="filters">
  <select id="fPortfolio" aria-label="Portfolio"><option value="">All portfolios</option>{opts(sorted(ex.portfolio_id.unique()))}</select>
  <select id="fCategory" aria-label="Category"><option value="">All root causes</option>{opts([b[0] for b in bars])}</select>
  <select id="fSeverity" aria-label="Severity"><option value="">All severities</option>{opts([s for s in SEV_ORDER if s in set(ex.severity)])}</select>
  <span class="muted" id="count"></span></div>
 <div class="scroll"><table id="ex"><thead><tr><th>Date</th><th>Portfolio</th><th>Security</th><th>Root cause</th>
  <th>Severity</th><th class="num">Impact (CAD)</th><th class="num">Age</th><th>Suggested action</th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table></div>
</section>
<section class="card" style="margin-top:16px"><h2>Data-quality issues</h2>
 <p class="cap">Flagged on the raw inputs before reconciliation.</p>
 <div class="scroll"><table><thead><tr><th>Date</th><th>Rule</th><th>Severity</th><th>Record</th><th>Detail</th></tr></thead>
 <tbody>{dq_rows}</tbody></table></div></section>
<footer>All portfolios, securities, prices and FX rates are simulated (no real client data). Accuracy is measured
against ground truth recorded when the data was generated: detection recall {ev['break_detection_recall']:.0%},
precision {ev['break_detection_precision']:.0%}; data-quality recall {ev['dq_recall']:.0%}, precision {ev['dq_precision']:.0%}.</footer>
</main><div id="tip" role="tooltip"></div>
<script>
const tip=document.getElementById('tip');
document.querySelectorAll('[data-tip]').forEach(el=>{{
  el.addEventListener('mousemove',e=>{{tip.textContent=el.dataset.tip;tip.style.opacity=1;
    tip.style.left=Math.min(e.clientX+12,innerWidth-290)+'px';tip.style.top=(e.clientY+12)+'px';}});
  el.addEventListener('mouseleave',()=>tip.style.opacity=0);}});
const f={{p:document.getElementById('fPortfolio'),c:document.getElementById('fCategory'),s:document.getElementById('fSeverity')}};
function apply(){{let n=0;document.querySelectorAll('#ex tbody tr').forEach(tr=>{{
  const ok=(!f.p.value||tr.dataset.portfolio===f.p.value)&&(!f.c.value||tr.dataset.category===f.c.value)&&(!f.s.value||tr.dataset.severity===f.s.value);
  tr.style.display=ok?'':'none';if(ok)n++;}});document.getElementById('count').textContent=n+' shown';}}
Object.values(f).forEach(x=>x.addEventListener('change',apply));apply();
</script></body></html>"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
    return path
