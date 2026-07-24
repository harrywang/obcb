"""Render scores.json as a self-contained HTML page.

The visual language is an editorial research-report style: serif headlines, a teal
accent, mono tabular numbers, hairline-bordered cards with soft shadows, stat tiles,
and callouts. It is adapted from a shared house style so OBCB reports read as one family.

Design rules kept deliberately, so later edits do not undo them:

*Light by default.* The page renders light regardless of the viewer's OS setting. A dark
variant exists behind an explicit ``data-theme="dark"`` opt-in — never automatic, so there
is no ``prefers-color-scheme`` block.

*Colour encodes the model.* Each solver keeps one hue across every chart, so the eye tracks
an entity rather than a rank. The teal accent is chrome (rules, links, the eyebrow), never a
data series. The two metrics are faceted into separate charts, not distinguished by colour.

*Values are labelled and a table ships.* Every bar carries its value in mono at the tip, and
the full numbers sit in a table, so nothing is gated behind colour perception. The series
palette was checked with a contrast validator.

No external requests: all CSS is inline and the only script is a tooltip handler.
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path

# Per-model data hues, validated for all-pairs separation in both modes. These are the
# only place data wears colour; everything else uses the reference chrome palette.
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"]

REPO_URL = "https://github.com/harrywang/obcb"
REPO_LABEL = "github.com/harrywang/obcb"

CSS = """
:root{
  --ground:#FAFBFC; --surface:#FFFFFF; --surface-2:#F3F6F8;
  --ink:#14202B; --muted:#586674; --faint:#8A97A2;
  --hair:#E3E8ED; --hair-strong:#D2DAE0;
  --accent:#146074; --accent-2:#1E8399; --accent-soft:#E5F0F3;
  --pass:#1F7A54; --pass-soft:#E4F1EA;
  --warn:#9A6612; --warn-soft:#F6EDDC;
  --alert:#A23A2E; --alert-soft:#F6E4E1;
  --shadow:0 1px 2px rgba(20,32,43,.04),0 6px 20px rgba(20,32,43,.05);
  --maxw:66rem;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:"SF Mono","JetBrains Mono",Menlo,Consolas,"Liberation Mono",monospace;
}
:root[data-theme="dark"]{
  --ground:#0D1418; --surface:#131D23; --surface-2:#182228;
  --ink:#E9EEF1; --muted:#9BA9B3; --faint:#6E7D87;
  --hair:#233039; --hair-strong:#2E3D47;
  --accent:#57B4CC; --accent-2:#7FCADD; --accent-soft:#16323B;
  --pass:#5FC395; --pass-soft:#142A20;
  --warn:#D9A445; --warn-soft:#2B2312;
  --alert:#E08A7C; --alert-soft:#33201C;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 8px 24px rgba(0,0,0,.28);
}
*{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);font-family:var(--sans);line-height:1.6;
  -webkit-font-smoothing:antialiased;margin:0;font-size:clamp(15px,.5vw + 14px,17px);}
.wrap{max-width:var(--maxw);margin:0 auto;padding:clamp(1.25rem,4vw,3rem);}
a{color:var(--accent-2);text-decoration:none}
a:hover{text-decoration:underline}
h1,h2,h3{font-family:var(--serif);font-weight:600;text-wrap:balance;letter-spacing:-.01em;
  line-height:1.15;margin:0}
.eyebrow{font-family:var(--mono);font-size:.72rem;letter-spacing:.18em;text-transform:uppercase;
  color:var(--accent);font-weight:600;margin:0 0 .9rem}
.tnum{font-variant-numeric:tabular-nums}
.mono{font-family:var(--mono)}

header.masthead{border-bottom:1px solid var(--hair);padding-bottom:2rem;margin-bottom:2.25rem}
h1{font-size:clamp(1.8rem,3.4vw,2.8rem)}
.lede{color:var(--muted);font-size:1.06rem;max-width:62ch;margin:1rem 0 0}
.meta{display:flex;flex-wrap:wrap;gap:.5rem 1.4rem;margin-top:1.4rem;font-family:var(--mono);
  font-size:.78rem;color:var(--faint);letter-spacing:.02em}
.meta span{display:inline-flex;align-items:center;gap:.45rem}
.dot{width:5px;height:5px;border-radius:50%;background:var(--accent-2);display:inline-block}
.repo-cta{display:inline-flex;align-items:center;gap:.6rem;margin-top:1.5rem;padding:.6rem 1rem;
  border-radius:10px;background:var(--accent);color:#fff;text-decoration:none;font-weight:600;
  font-size:.92rem;box-shadow:var(--shadow);transition:background .15s,transform .15s}
.repo-cta:hover{background:var(--accent-2);transform:translateY(-1px)}
.repo-cta svg{flex:none}

.stats{display:grid;grid-template-columns:repeat(var(--ntiles,4),1fr);gap:1px;
  background:var(--hair);border:1px solid var(--hair);border-radius:14px;overflow:hidden;
  box-shadow:var(--shadow)}
.stat{background:var(--surface);padding:1.35rem 1.25rem;min-width:0}
.stat .k{font-family:var(--mono);font-size:clamp(1.5rem,3vw,2.1rem);font-weight:600;
  color:var(--ink);letter-spacing:-.02em;line-height:1}
.stat .k .u{font-size:.9rem;color:var(--faint);font-weight:500}
.stat .l{font-size:.8rem;color:var(--muted);margin-top:.55rem;line-height:1.35}
@media (max-width:640px){.stats{grid-template-columns:repeat(2,1fr)}}

section{margin-top:3rem}
h2{font-size:clamp(1.3rem,2.2vw,1.7rem);margin-bottom:.4rem}
h3{font-family:var(--mono);font-size:.74rem;letter-spacing:.12em;text-transform:uppercase;
  color:var(--accent);font-weight:700;margin:1.6rem 0 .7rem}
.section-note{color:var(--muted);max-width:66ch;margin:0 0 1.4rem}
ul.notes{color:var(--muted);max-width:72ch;margin:0 0 1.2rem;padding-left:1.15rem}
ul.notes li{margin:.3rem 0}

.legend{display:flex;flex-wrap:wrap;gap:.5rem 1.4rem;margin:0 0 1.1rem}
.legend span{display:inline-flex;align-items:center;gap:.5rem;font-size:.86rem;color:var(--muted)}
.swatch{width:11px;height:11px;border-radius:3px;flex:none}

.chart{margin:.2rem 0 .6rem}
.grp{margin:0 0 1rem}
.grp .gname{font-family:var(--mono);font-size:.74rem;letter-spacing:.12em;text-transform:uppercase;
  color:var(--accent);font-weight:700;margin:1.1rem 0 .4rem}
.row{display:grid;grid-template-columns:var(--labelw,200px) 1fr 62px;align-items:center;
  gap:.9rem;padding:.22rem 0}
.row .lab{font-size:.86rem;color:var(--muted);text-align:right;overflow-wrap:anywhere}
.track{position:relative;height:20px;
  background:linear-gradient(to right,var(--hair) 0 1px,transparent 1px) repeat-x;
  background-size:25% 100%}
.bar{position:absolute;left:0;top:3px;height:14px;border-radius:0 4px 4px 0}
.costbar{position:absolute;left:0;top:3px;height:14px;display:flex;overflow:hidden;
  border-radius:0 4px 4px 0}
.seg{height:100%}
.seg.c-constr,.cswatch.c-constr{background:var(--accent-2)}
.seg.c-solver,.cswatch.c-solver{background:var(--accent)}
.seg.c-judge,.cswatch.c-judge{background:var(--warn)}
.linechart{width:100%;height:auto;min-width:480px;margin:.2rem 0 .6rem;overflow:visible}
.linechart .grid{stroke:var(--hair);stroke-width:1}
.linechart .axis{stroke:var(--hair-strong);stroke-width:1}
.linechart .axlab{fill:var(--faint);font-family:var(--mono);font-size:11px}
.linechart .axnum{fill:var(--accent);font-family:var(--mono);font-size:12px;font-weight:700}
.linechart circle{transition:r .12s}
.linechart circle:hover{r:5}
.ci{position:absolute;top:9px;height:2px;background:var(--faint);opacity:.7;border-radius:1px}
.ci::before,.ci::after{content:"";position:absolute;top:-3px;width:2px;height:8px;
  background:var(--faint)}
.ci::before{left:0}.ci::after{right:0}
.row .val{font-family:var(--mono);font-size:.86rem;font-variant-numeric:tabular-nums;
  color:var(--ink);text-align:right}

.tablecard{border:1px solid var(--hair);border-radius:14px;overflow:hidden;
  box-shadow:var(--shadow);background:var(--surface);margin-top:.4rem}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%}
thead th{font-family:var(--mono);font-size:.68rem;letter-spacing:.12em;text-transform:uppercase;
  color:var(--faint);font-weight:600;text-align:right;padding:.8rem 1.1rem;
  background:var(--surface-2);border-bottom:1px solid var(--hair-strong)}
thead th:first-child{text-align:left}
tbody td{padding:.85rem 1.1rem;border-bottom:1px solid var(--hair);text-align:right;
  font-variant-numeric:tabular-nums;font-family:var(--mono);font-size:.9rem}
tbody td:first-child{text-align:left;font-family:var(--sans)}
tbody tr:last-child td{border-bottom:none}
tbody tr.total td{border-top:2px solid var(--hair);border-bottom:none}
tbody tr:hover{background:var(--surface-2)}

.callout{display:flex;gap:1rem;align-items:flex-start;background:var(--warn-soft);
  border:1px solid color-mix(in srgb,var(--warn) 30%,transparent);border-radius:12px;
  padding:1.15rem 1.3rem;margin-top:1.75rem;color:var(--ink)}
.callout .ic{flex:none;width:24px;height:24px;color:var(--warn)}
.callout p{margin:0;font-size:.95rem}
.callout strong{color:var(--warn)}

.terminal{background:#0E1519;color:#CBD6DE;border-radius:12px;padding:1.1rem 1.25rem;
  font-family:var(--mono);font-size:.83rem;line-height:1.7;overflow-x:auto;
  border:1px solid #1E2A32;margin-top:.4rem;white-space:pre}
.terminal .c{color:#5E7280}
.terminal .g{color:#5FC395}
.terminal b{color:#7FCADD;font-weight:600}

/* per-case / per-question detail */
/* number -> case-title key under the by-case charts (collapsed by default) */
.keybox{margin:.8rem 0 .2rem}
.keybox>summary{cursor:pointer;list-style:none;display:inline-flex;align-items:center;gap:.4rem;
  font-family:var(--mono);font-size:.74rem;letter-spacing:.08em;text-transform:uppercase;
  font-weight:700;color:var(--accent)}
.keybox>summary::-webkit-details-marker{display:none}
.keybox>summary::before{content:"▸";font-size:.8rem;transition:transform .15s}
.keybox[open]>summary::before{content:"▾"}
.casekey{display:grid;grid-template-columns:repeat(auto-fit,minmax(19rem,1fr));
  gap:.35rem 1.4rem;margin:.7rem 0 .2rem;font-size:.82rem;color:var(--muted)}
.casekey span{display:flex;gap:.55rem;align-items:baseline}
.knum,.cnum{font-family:var(--mono);font-size:.72rem;font-weight:700;color:var(--accent);
  background:var(--accent-soft);border-radius:5px;padding:.1rem .38rem;flex:none;
  min-width:1.5rem;text-align:center}
details.case{border:1px solid var(--hair);border-radius:12px;background:var(--surface);
  box-shadow:var(--shadow);margin:.7rem 0;overflow:hidden}
details.case>summary{cursor:pointer;list-style:none;padding:1rem 1.3rem;
  background:var(--surface-2);font-family:var(--serif);font-size:1.06rem;font-weight:600;
  display:flex;justify-content:space-between;gap:1rem;align-items:baseline}
details.case>summary::-webkit-details-marker{display:none}
details.case>summary .ctitle{flex:1;text-align:left}
details.case>summary .qn{font-family:var(--mono);font-size:.72rem;color:var(--faint);
  font-weight:600;white-space:nowrap}
.qblock{padding:1.1rem 1.3rem;border-top:1px solid var(--hair)}
.qblock:first-of-type{border-top:none}
.qmeta{font-family:var(--mono);font-size:.7rem;letter-spacing:.06em;color:var(--faint);
  text-transform:uppercase;margin:0 0 .5rem}
.qtext{font-size:.95rem;margin:0 0 .9rem;white-space:pre-wrap}
.sub{font-family:var(--mono);font-size:.66rem;letter-spacing:.1em;text-transform:uppercase;
  color:var(--accent);font-weight:600;margin:.9rem 0 .4rem}
.qblock .sub:first-of-type{margin-top:.3rem}
.soln{font-size:.9rem;color:var(--muted);white-space:pre-wrap;background:var(--surface-2);
  border-radius:8px;padding:.7rem .9rem;margin:0}
.rubric{list-style:none;margin:.2rem 0 0;padding:0}
.rubric li{font-size:.88rem;color:var(--ink);padding:.24rem 0 .24rem 1.5rem;position:relative}
.rubric li::before{content:"\\2713";position:absolute;left:0;color:var(--accent);
  font-weight:700}
.ans{margin-top:.5rem;border-top:1px dashed var(--hair);padding-top:.6rem}
.ans .hd{display:flex;align-items:center;gap:.7rem;flex-wrap:wrap}
.ans .hd .sw{width:9px;height:9px;border-radius:3px;flex:none}
.ans .hd .nm{font-size:.86rem;color:var(--ink)}
.ans .hd .sc{font-family:var(--mono);font-size:.82rem;color:var(--muted);
  font-variant-numeric:tabular-nums}
.ans details>summary{cursor:pointer;font-size:.8rem;color:var(--accent-2);margin-top:.35rem}
.ans .body{white-space:pre-wrap;font-size:.86rem;color:var(--muted);margin-top:.5rem;
  background:var(--surface-2);border-radius:8px;padding:.7rem .9rem}

footer{margin-top:3.5rem;padding-top:1.5rem;border-top:1px solid var(--hair);color:var(--faint);
  font-size:.82rem;display:flex;flex-wrap:wrap;gap:.4rem 1.2rem;justify-content:space-between}
footer a{color:var(--accent);text-decoration:none}
footer a:hover{text-decoration:underline}

#tip{position:fixed;pointer-events:none;opacity:0;transition:opacity .1s;background:var(--ink);
  color:var(--ground);font-size:12.5px;padding:6px 9px;border-radius:6px;z-index:9;
  white-space:nowrap;font-family:var(--mono);font-variant-numeric:tabular-nums}
@media (max-width:620px){.row{grid-template-columns:1fr;gap:.1rem}.row .lab{text-align:left}}
"""

JS = """
(function () {
  var tip = document.getElementById('tip');
  document.querySelectorAll('[data-tip]').forEach(function (el) {
    el.addEventListener('mousemove', function (e) {
      tip.textContent = el.getAttribute('data-tip'); tip.style.opacity = 1;
      var x = e.clientX + 14, y = e.clientY + 16;
      if (x + tip.offsetWidth > innerWidth - 8) x = e.clientX - tip.offsetWidth - 12;
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    });
    el.addEventListener('mouseleave', function () { tip.style.opacity = 0; });
  });
})();
"""

STRATA = {
    "numerical": "Numerical",
    "non_numerical": "Non-numerical",
    "subjective": "Subjective",
    "objective": "Objective",
    "fictional_case": "Fictional case",
    "real_case": "Real case",
}


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _dur(seconds: float) -> str:
    """Compact duration: 42s, 3m 20s, 1h 04m."""
    s = int(round(seconds or 0))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def _bar_row(
    label: str, stat: dict, color_idx: int, labelw: int = 200, tip_label: str | None = None
) -> str:
    v = stat["standard"] if "standard" in stat else stat["value"]
    lo, hi = stat.get("ci", (v, v))
    tip = (
        f"{tip_label or label} — {_pct(v)}  "
        f"(95% CI {_pct(lo)}–{_pct(hi)}, n={stat.get('n', '?')})"
    )
    ci = ""
    if hi > lo:
        ci = f'<div class="ci" style="left:{lo * 100:.2f}%;right:{(1 - hi) * 100:.2f}%"></div>'
    return (
        f'<div class="row" style="--labelw:{labelw}px" data-tip="{_e(tip)}">'
        f'<div class="lab">{_e(label)}</div>'
        f'<div class="track"><div class="bar" style="width:{v * 100:.2f}%;'
        f'background:var(--s{color_idx})"></div>{ci}</div>'
        f'<div class="val">{_pct(v)}</div></div>'
    )


def _metric_chart(ranked: list, key: str, colors: dict) -> str:
    rows = [
        _bar_row(
            m,
            {"value": r["overall"][key], "ci": r["overall"][f"{key}_ci"], "n": r["overall"]["n"]},
            colors[m],
        )
        for m, r in ranked
    ]
    return f'<div class="chart">{"".join(rows)}</div>'


def _legend(ranked: list, colors: dict) -> str:
    items = "".join(
        f'<span><i class="swatch" style="background:var(--s{colors[m]})"></i>{_e(m)}</span>'
        for m, _ in ranked
    )
    return f'<div class="legend">{items}</div>'


def _grouped(
    report: dict, section: str, ranked: list, colors: dict, labels: dict | None = None
) -> str:
    cats: list[str] = []
    for _, res in ranked:
        for c in res[section]:
            if c not in cats:
                cats.append(c)

    def mean_of(cat: str) -> float:
        vals = [r[section][cat]["standard"] for _, r in ranked if cat in r[section]]
        return sum(vals) / len(vals) if vals else 0.0

    cats.sort(key=mean_of)
    out = []
    for cat in cats:
        rows = []
        for model, res in ranked:
            st = res[section].get(cat)
            if not st:
                continue
            rows.append(
                _bar_row(
                    model,
                    {"value": st["standard"], "ci": st["standard_ci"], "n": st["n"]},
                    colors[model],
                    tip_label=f"{model} · {(labels or {}).get(cat, cat)}",
                )
            )
        if not rows:
            continue
        n = max((r[section][cat]["n"] for _, r in ranked if cat in r[section]), default=0)
        name = (labels or {}).get(cat, cat)
        out.append(
            f'<div class="grp"><div class="gname">{_e(name)} · n={n}</div>{"".join(rows)}</div>'
        )
    return "".join(out)


def _nice_domain(values, cap_hi: float | None = None, include_zero: bool = False):
    """Frame the data in a readable y-range: (lo, hi, ticks) on round step boundaries.

    A fixed 0-100% axis wastes most of the plot when every score sits in a narrow band, so
    the range is snapped outward to the nearest round step instead of starting at zero.
    ``include_zero`` keeps the zero baseline for magnitudes (cost), where a floating
    baseline would misrepresent how large the values actually are.
    """
    vals = [v for v in values if v is not None]
    if include_zero:
        vals.append(0.0)
    if not vals:
        return 0.0, 1.0, [0.0, 0.25, 0.5, 0.75, 1.0]
    dmin, dmax = min(vals), max(vals)
    if cap_hi is not None:
        dmax = min(dmax, cap_hi)
    span = dmax - dmin
    if span <= 0:
        span = abs(dmax) or 1.0
    raw = span / 5
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if raw <= m * mag), 10 * mag)
    lo = max(math.floor(dmin / step) * step, 0.0)
    hi = math.ceil(dmax / step) * step
    if cap_hi is not None:
        hi = min(hi, cap_hi)
    ticks, t = [], lo
    while t <= hi + step * 1e-6:
        ticks.append(round(t, 10))
        t += step
    return lo, hi, ticks


def _line_chart(x_labels: list[str], series: list[dict], y_min: float, y_max: float,
                y_ticks: list[float], fmt, x_tips: list[str] | None = None) -> str:
    """A self-contained SVG line chart. x = cases (categorical), y = metric.

    ``series`` items are ``{"label", "color", "width", "points": [y or None ...]}`` aligned
    to ``x_labels``. ``fmt`` formats a y value for axis ticks and point tooltips. ``x_tips``
    supplies the hover text for each x position when the axis label is a short stand-in
    (a case number). No JS, no libraries: axes, gridlines, polylines, and hover-title
    points are all inline SVG.
    """
    x_tips = x_tips or x_labels
    W, H = 800, 260
    PADL, PADR, PADT, PADB = 50, 16, 14, 40
    pw, ph = W - PADL - PADR, H - PADT - PADB
    n = len(x_labels)

    def xat(i: int) -> float:
        return PADL + (pw * i / (n - 1) if n > 1 else pw / 2)

    span = (y_max - y_min) or 1.0

    def yat(v: float) -> float:
        return PADT + ph * (1 - (v - y_min) / span)

    parts = [f'<line x1="{PADL}" y1="{PADT+ph:.1f}" x2="{W-PADR}" y2="{PADT+ph:.1f}" class="axis"/>']
    for t in y_ticks:
        y = yat(t)
        parts.append(f'<line x1="{PADL}" y1="{y:.1f}" x2="{W-PADR}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{PADL-8}" y="{y+3:.1f}" text-anchor="end" class="axlab">{_e(fmt(t))}</text>')
    for i, lab in enumerate(x_labels):
        x = xat(i)
        yl = PADT + ph + 20
        parts.append(
            f'<text x="{x:.1f}" y="{yl:.1f}" text-anchor="middle" class="axnum">'
            f'<title>{_e(x_tips[i])}</title>{_e(lab)}</text>'
        )
    for s in series:
        coords = [(i, xat(i), yat(v), v) for i, v in enumerate(s["points"]) if v is not None]
        if len(coords) >= 2:
            poly = " ".join(f"{x:.1f},{y:.1f}" for _, x, y, _ in coords)
            parts.append(
                f'<polyline points="{poly}" fill="none" stroke="{s["color"]}" '
                f'stroke-width="{s.get("width", 2.4)}" stroke-linejoin="round" stroke-linecap="round"/>'
            )
        for i, x, y, v in coords:
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.4" fill="{s["color"]}">'
                f'<title>{_e(s["label"])} · {_e(x_tips[i])} · {_e(fmt(v))}</title></circle>'
            )
    return (
        '<div class="scroll"><svg viewBox="0 0 {w} {h}" class="linechart" '
        'preserveAspectRatio="xMidYMid meet" role="img">{body}</svg></div>'
    ).format(w=W, h=H, body="".join(parts))


def _by_case_section(report: dict, cost: dict | None, ranked: list, colors: dict) -> str:
    """Score and cost across cases as line charts (x = case, y = score / cost).

    Score: one line per model (Standard score per case). Cost: one line for the case total
    plus one each for construction / solver / judge.
    """
    details = report.get("cases_detail", [])
    titles = {c["case_name"]: c.get("case_title", c["case_name"]) for c in details}
    numbers = {c["case_name"]: c.get("case_index") for c in details}
    per_case = (cost or {}).get("per_case", [])

    # One case order shared by both charts, so the x-axes line up. Ordered by position in
    # the case list, which is also the number each case is labelled with.
    case_order: list[str] = [c["case_name"] for c in details]
    for _, r in ranked:
        for cn in r.get("by_case", {}):
            if cn not in case_order:
                case_order.append(cn)
    for pc in per_case:  # include any case that has cost but somehow no score row
        if pc["case"] not in case_order:
            case_order.append(pc["case"])
    if len(case_order) < 2:
        return ""  # a line chart needs at least two cases to be meaningful
    # Always ascending by case number, whatever order the sources happened to be in.
    case_order.sort(key=lambda cn: (numbers.get(cn) is None, numbers.get(cn) or 0))

    # x-axis is the case number; the key below maps number -> title.
    x_labels = [str(numbers.get(cn) or i + 1) for i, cn in enumerate(case_order)]
    tips = [
        f"{numbers.get(cn) or i + 1} · {titles.get(cn, cn)}" for i, cn in enumerate(case_order)
    ]
    key_items = "".join(
        f'<span><b class="knum">{_e(str(numbers.get(cn) or i + 1))}</b>{_e(titles.get(cn, cn))}</span>'
        for i, cn in enumerate(case_order)
    )
    case_key = (
        f'<details class="keybox"><summary>Case key ({len(case_order)})</summary>'
        f'<div class="casekey">{key_items}</div></details>'
    )

    # --- score by case: one line per model ---
    score_series = [
        {
            "label": model,
            "color": f"var(--s{colors[model]})",
            "points": [
                (r["by_case"][cn]["standard"] if cn in r.get("by_case", {}) else None)
                for cn in case_order
            ],
        }
        for model, r in ranked
    ]
    # Scores cluster in a narrow band, so frame the axis to the data (capped at 100%)
    # rather than always starting at zero.
    s_lo, s_hi, s_ticks = _nice_domain(
        [v for s in score_series for v in s["points"]], cap_hi=1.0
    )
    score_chart = (
        "<h3>Standard score by case</h3>"
        f"{_legend(ranked, colors)}"
        f'{_line_chart(x_labels, score_series, s_lo, s_hi, s_ticks, _pct, tips)}'
    )

    # --- cost by case: total plus the three-way split ---
    cost_chart = ""
    if per_case:
        pc_by_case = {pc["case"]: pc for pc in per_case}

        def col(key: str) -> list:
            return [(pc_by_case[cn][key] if cn in pc_by_case else None) for cn in case_order]

        cost_series = [
            {"label": "Total", "color": "var(--ink)", "width": 3.0, "points": col("total_usd")},
            {"label": "Construction", "color": "var(--accent-2)", "points": col("construction_usd")},
            {"label": "Solver", "color": "var(--accent)", "points": col("solver_usd")},
            {"label": "Judge", "color": "var(--warn)", "points": col("judge_usd")},
        ]
        # Cost keeps its zero baseline: one series sits near $0, and a floating baseline
        # would overstate how big those values are.
        c_lo, c_hi, c_ticks = _nice_domain(
            [v for s in cost_series for v in s["points"]], include_zero=True
        )
        cost_legend = (
            '<div class="legend">'
            '<span><i class="swatch" style="background:var(--ink)"></i>Total</span>'
            '<span><i class="swatch cswatch c-constr"></i>Construction</span>'
            '<span><i class="swatch cswatch c-solver"></i>Solver</span>'
            '<span><i class="swatch cswatch c-judge"></i>Judge</span></div>'
        )
        cost_chart = (
            "<h3>Cost by case</h3>"
            f"{cost_legend}"
            f'{_line_chart(x_labels, cost_series, c_lo, c_hi, c_ticks, lambda v: f"${v:.2f}", tips)}'
        )

    return (
        '<section><h2>By case</h2>'
        '<p class="section-note">Standard score and spend per case. Cases are numbered by their '
        "position in the case list; hover a point for the exact value.</p>"
        f"{score_chart}{cost_chart}{case_key}</section>"
    )


def _detail_section(cases: list[dict], colors: dict, dropped: list[str] | None = None) -> str:
    """Collapsible per-case detail: each question's reference solution, rubric, and how
    each model answered and scored. This is the builder's output, made inspectable."""
    if not cases:
        return ""
    blocks = []
    for idx, case in enumerate(cases):
        qs = case.get("questions", [])
        qblocks = []
        for q in qs:
            tags = " · ".join(
                filter(
                    None,
                    [
                        q.get("discipline"),
                        "numerical" if q.get("numerical") else None,
                        "subjective" if q.get("subjective") else "objective",
                        q.get("intermediate_work_activity"),
                    ],
                )
            )
            rubric = "".join(f"<li>{_e(c)}</li>" for c in q.get("grading_rubric", []))
            answers = []
            for a in q.get("answers", []):
                ci = colors.get(a["model"], 1)
                sc = f'{a["graded_score"]}/{a["n_criteria"]} · {_pct(a["standard_score"])}'
                body = (
                    f'<details><summary>show answer</summary>'
                    f'<div class="body">{_e(a["answer"]) or "<em>(no answer)</em>"}</div></details>'
                    if a.get("answer")
                    else ""
                )
                answers.append(
                    f'<div class="ans"><div class="hd">'
                    f'<i class="sw" style="background:var(--s{ci})"></i>'
                    f'<span class="nm">{_e(a["model"])}</span>'
                    f'<span class="sc">{sc}</span></div>{body}</div>'
                )
            qblocks.append(
                f'<div class="qblock">'
                f'<p class="qmeta">{_e(tags)}</p>'
                f'<p class="sub">Question</p><p class="qtext">{_e(q["question"])}</p>'
                f'<p class="sub">Reference solution</p><p class="soln">{_e(q["solution"])}</p>'
                f'<p class="sub">Rubric</p><ul class="rubric">{rubric}</ul>'
                f'<p class="sub">Model answers</p>{"".join(answers)}'
                f"</div>"
            )
        n = len(qs)
        num = case.get("case_index") or idx + 1
        blocks.append(
            f'<details class="case" data-case="{_e(case["case_name"])}">'
            f'<summary><span class="cnum">{_e(str(num))}</span>'
            f'<span class="ctitle">{_e(case["case_title"])}</span>'
            f'<span class="qn">{n} question{"s" if n != 1 else ""}</span></summary>'
            f'{"".join(qblocks)}</details>'
        )

    note = ""
    if dropped:
        items = "".join(f"<li>{_e(c)}</li>" for c in dropped)
        note = (
            f'<details class="keybox"><summary>{len(dropped)} case(s) yielded no questions'
            "</summary>"
            '<p class="section-note" style="margin:.6rem 0 .4rem">These cases were fetched and '
            "built (so they carry construction cost), but every candidate question was dropped by "
            "the quality gates — no explicit reference solution in the teaching note, or no usable "
            "rubric. They are not part of the scored benchmark.</p>"
            f'<ul class="rubric">{items}</ul></details>'
        )
    return (
        "<section><h2>Case &amp; question detail</h2>"
        '<p class="section-note">The benchmark itself: each extracted question with its '
        "reference solution and checklist rubric, and how every model answered and scored. "
        "Click a case to expand.</p>"
        f'{note}{"".join(blocks)}</section>'
    )


def render(report: dict, title: str = "Open Business Case Bench") -> str:
    models = report["models"]
    ranked = sorted(models.items(), key=lambda kv: kv[1]["overall"]["standard"], reverse=True)
    colors = {m: i + 1 for i, (m, _) in enumerate(ranked)}
    cfg = report.get("config", {})
    cost = report.get("cost")

    series_vars = "".join(f"--s{i + 1}:{c};" for i, c in enumerate(SERIES_LIGHT))
    series_dark = "".join(f"--s{i + 1}:{c};" for i, c in enumerate(SERIES_DARK))

    best_std = ranked[0]
    best_cas = max(r["overall"]["complete_answer"] for _, r in ranked)
    n_cases = report.get("n_cases", len(report.get("cases_detail", [])))
    n_built = report.get("n_cases_built", n_cases)
    # Show "50 / 54" when some built cases yielded no usable questions, so the gap is
    # explicit rather than looking like cases went missing.
    tiles = [
        (f"{n_cases}", f" / {n_built}" if n_built > n_cases else "",
         f'Case{"s" if n_cases != 1 else ""} in the benchmark'),
        (f'{report["n_questions"]}', "", "Questions in the benchmark"),
        (_pct(best_std[1]["overall"]["standard"]), "", "Best Standard score"),
        (_pct(best_cas), "", "Best Complete Answer score"),
    ]
    if cost:
        tiles.append((f'${cost["total_usd"]:.2f}', "", f'Spend across {cost["runs"]} run(s)'))
    tile_html = "".join(
        f'<div class="stat"><div class="k tnum">{_e(v)}<span class="u">{_e(u)}</span></div>'
        f'<div class="l">{_e(lbl)}</div></div>'
        for v, u, lbl in tiles
    )

    head = (
        "<tr><th>Model</th><th>Standard</th><th>95% CI</th>"
        "<th>Complete Answer</th><th>95% CI</th><th>n</th></tr>"
    )
    body = "".join(
        "<tr><td>{m}</td><td>{s}</td><td>{sc}</td><td>{c}</td><td>{cc}</td><td>{n}</td></tr>".format(
            m=_e(model),
            s=_pct(r["overall"]["standard"]),
            sc=f'{_pct(r["overall"]["standard_ci"][0])}–{_pct(r["overall"]["standard_ci"][1])}',
            c=_pct(r["overall"]["complete_answer"]),
            cc=f'{_pct(r["overall"]["complete_answer_ci"][0])}–'
            f'{_pct(r["overall"]["complete_answer_ci"][1])}',
            n=r["overall"]["n"],
        )
        for model, r in ranked
    )

    cost_block = ""
    if cost:
        constr = cost.get("construction_usd", 0.0)
        solver = cost.get("solver_usd", 0.0)
        judge = cost.get("judge_usd", 0.0)
        total = cost["total_usd"]

        # Headline: the three categories the total decomposes into.
        cat_rows = "".join(
            f"<tr><td>{_e(name)}</td><td>${amt:.4f}</td><td>{(amt / total * 100 if total else 0):.0f}%</td></tr>"
            for name, amt in [
                ("Construction (extract + build)", constr),
                ("Solver (answers under test)", solver),
                ("Judge (grading)", judge),
            ]
        )
        category_table = (
            '<h3>By category</h3><div class="tablecard"><div class="scroll"><table>'
            "<thead><tr><th>Category</th><th>Spend</th><th>Share</th></tr></thead>"
            f"<tbody>{cat_rows}"
            f'<tr class="total"><td><strong>Total</strong></td>'
            f"<td><strong>${total:.4f}</strong></td><td></td></tr></tbody>"
            "</table></div></div>"
        )

        # Per benchmarked model: its own solve cost and the judge's cost to grade it.
        model_table = ""
        by_model_eval = cost.get("by_model_eval", {})
        if by_model_eval:
            rows = "".join(
                f"<tr><td>{_e(m)}</td><td>${v['solve_usd']:.4f}</td>"
                f"<td>${v['judge_usd']:.4f}</td><td>${v['total_usd']:.4f}</td></tr>"
                for m, v in by_model_eval.items()
            )
            model_table = (
                '<h3>By model</h3>'
                '<p class="section-note">Each benchmarked model\'s own solve cost, and the fixed '
                "judge's cost to grade its answers. Construction is excluded — it is shared, not "
                "charged to any model.</p>"
                '<div class="tablecard"><div class="scroll"><table>'
                "<thead><tr><th>Model</th><th>Solve</th><th>Judge</th><th>Total</th></tr></thead>"
                f"<tbody>{rows}</tbody></table></div></div>"
            )

        # Per case: construction (shared) + solver + judge → case total.
        per_case = cost.get("per_case", [])
        case_table = ""
        if per_case:
            body_rows = "".join(
                f'<tr><td>{_e(c["case"])}</td>'
                f'<td>${c["construction_usd"]:.4f}</td>'
                f'<td>${c["solver_usd"]:.4f}</td>'
                f'<td>${c["judge_usd"]:.4f}</td>'
                f'<td>${c["total_usd"]:.4f}</td>'
                f'<td>{_e(_dur(c.get("seconds", 0.0)))}</td></tr>'
                for c in per_case
            )
            case_table = (
                '<h3>By case</h3>'
                '<p class="section-note">Construction (extract + build) runs once per case and is '
                "shared; then every benchmarked model adds solve + judge. "
                "Case total = construction + solver + judge. Time is summed model time for that "
                "case — calls run concurrently, so it exceeds wall clock.</p>"
                '<div class="tablecard"><div class="scroll"><table>'
                "<thead><tr><th>Case</th><th>Construction</th><th>Solver</th>"
                "<th>Judge</th><th>Total</th><th>Time</th></tr></thead>"
                f"<tbody>{body_rows}</tbody></table></div></div>"
            )

        cost_block = (
            "<section><h2>Cost</h2>"
            f"{category_table}{model_table}{case_table}</section>"
        )

    conf_rows = "".join(
        f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>"
        for k, v in [
            ("judge", report.get("judge_model", "—")),
            ("builder", cfg.get("models", {}).get("builder", "—")),
            ("annotator", cfg.get("models", {}).get("annotator", "—")),
            ("extractor", report.get("extractor_used") or cfg.get("extraction", {}).get("extractor", "—")),
            ("temperature", cfg.get("sampling", {}).get("temperature", "—")),
            ("solver max tokens", cfg.get("sampling", {}).get("solver_max_tokens", "—")),
            ("judge max tokens", cfg.get("sampling", {}).get("judge_max_tokens", "—")),
            ("min rubric criteria", cfg.get("quality_gates", {}).get("min_rubric_criteria", "—")),
            (
                "bootstrap",
                f'B={cfg.get("reporting", {}).get("bootstrap_b", "—")}, '
                f'seed {cfg.get("reporting", {}).get("bootstrap_seed", "—")}',
            ),
        ]
    )

    return f"""<style>{CSS}
:root{{ {series_vars} }}
:root[data-theme="dark"]{{ {series_dark} }}
</style>
<div class="wrap">
<header class="masthead">
  <p class="eyebrow">Benchmark Results · Open Business Case Bench</p>
  <h1>{_e(title)}</h1>
  <p class="lede">LLMs scored on open-ended business-case questions, each graded against an
    instructor-derived checklist rubric by a fixed LLM-as-judge.</p>
  <a class="repo-cta" href="{REPO_URL}">
    <svg viewBox="0 0 16 16" width="18" height="18" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
    <span>View source on GitHub</span>
  </a>
</header>

<div class="stats" role="list" aria-label="Headline results" style="--ntiles:{len(tiles)}">{tile_html}</div>

<section>
  <h2>Overall</h2>
  <ul class="notes">
    <li><strong>Standard scoring</strong> — the rubric-weighted fraction of criteria an answer
      satisfies.</li>
    <li><strong>Complete Answer scoring</strong> — stricter: the share of questions where every
      criterion is met.</li>
  </ul>
  <p class="section-note">Whiskers are bootstrap 95% confidence intervals.</p>
  {_legend(ranked, colors)}
  <h3>Standard scoring</h3>
  {_metric_chart(ranked, "standard", colors)}
  <h3>Complete Answer scoring</h3>
  {_metric_chart(ranked, "complete_answer", colors)}
  <div class="tablecard"><div class="scroll"><table>
    <thead>{head}</thead><tbody>{body}</tbody>
  </table></div></div>
</section>

<section>
  <h2>By question type</h2>
  <p class="section-note">Standard scoring within each stratum, weakest first.</p>
  {_legend(ranked, colors)}
  {_grouped(report, "by_question_type", ranked, colors, STRATA)}
</section>

<section>
  <h2>By discipline</h2>
  <p class="section-note">Standard scoring, weakest discipline first.</p>
  {_legend(ranked, colors)}
  {_grouped(report, "by_discipline", ranked, colors)}
</section>

{_by_case_section(report, cost, ranked, colors)}

{cost_block}

{_detail_section(report.get("cases_detail", []), colors, report.get("cases_without_questions"))}

<section>
  <h2>How this run was configured</h2>
  <p class="section-note">Every setting that can change these numbers. The full resolved
    configuration is in <code>run_config.json</code>.</p>
  <div class="tablecard"><div class="scroll"><table>
    <thead><tr><th>Setting</th><th>Value</th></tr></thead><tbody>{conf_rows}</tbody>
  </table></div></div>
</section>

<footer>
  <span>Open Business Case Bench &middot; <a href="{REPO_URL}">{REPO_LABEL}</a></span>
  <span class="mono">{report["n_questions"]} questions &middot; {len(models)} models</span>
</footer>
</div>
<div id="tip"></div>
<script>{JS}</script>
"""


def write(report: dict, path: Path, title: str = "Open Business Case Bench") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(title)}</title></head><body style='margin:0'>"
        f"{render(report, title)}</body></html>"
    )
    path.write_text(page, encoding="utf-8")
    return path


def write_from_scores(scores_path: Path, out_path: Path) -> Path:
    return write(json.loads(scores_path.read_text(encoding="utf-8")), out_path)
