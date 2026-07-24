"""Assert the HTML report renders correctly and stays self-contained.

The page is meant to be openable from disk and shareable as one file, so the checks
that matter are: no external requests, every datum present, and untrusted strings
(model names come from a config the user controls) escaped rather than injected.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from obcb import html_report

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    ok = ok and cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")


def stat(v: float, n: int = 10) -> dict:
    return {
        "n": n, "standard": v, "standard_ci": [max(0.0, v - 0.05), min(1.0, v + 0.05)],
        "complete_answer": max(0.0, v - 0.3),
        "complete_answer_ci": [max(0.0, v - 0.35), max(0.0, v - 0.25)],
    }


REPORT = {
    "n_questions": 42,
    "judge_model": "vendor/judge",
    "config": {
        "models": {"builder": "vendor/b", "annotator": "vendor/a", "judge": "vendor/judge"},
        "sampling": {"temperature": 0.0, "solver_max_tokens": 10000, "judge_max_tokens": 10000},
        "quality_gates": {"min_rubric_criteria": 2},
        "extraction": {"extractor": "pymupdf4llm"},
        "reporting": {"bootstrap_b": 500, "bootstrap_seed": 1},
        "prompts": {"RUBRIC_PROMPT": "abc123abc123"},
    },
    "models": {
        "vendor/alpha": {
            "overall": stat(0.88, 42),
            "by_discipline": {"Finance": stat(0.9, 20), "Strategy": stat(0.8, 22)},
            "by_case": {"c1": stat(0.9, 21), "c2": stat(0.86, 21)},
            "by_question_type": {"numerical": stat(0.85, 18), "subjective": stat(0.9, 24)},
        },
        "<script>alert(1)</script>": {
            "overall": stat(0.55, 42),
            "by_discipline": {"Finance": stat(0.5, 20), "Strategy": stat(0.6, 22)},
            "by_case": {"c1": stat(0.5, 21), "c2": stat(0.6, 21)},
            "by_question_type": {"numerical": stat(0.5, 18), "subjective": stat(0.6, 24)},
        },
    },
    "cost": {
        "runs": 2, "total_usd": 1.25, "saved_usd": 0.5,
        "prompt_tokens": 1000, "completion_tokens": 200,
        "construction_usd": 0.3, "solver_usd": 0.8, "judge_usd": 0.15,
        "by_model": {"vendor/alpha": 1.0},
        "by_model_eval": {"vendor/alpha": {"solve_usd": 0.5, "judge_usd": 0.1, "total_usd": 0.6}},
        "per_case": [
            {"case": "c1", "total_usd": 0.7, "construction_usd": 0.2, "solver_usd": 0.45,
             "judge_usd": 0.05, "by_model": {"vendor/alpha": {"solve_usd": 0.45, "judge_usd": 0.05,
                                                              "total_usd": 0.5}}},
            {"case": "c2", "total_usd": 0.55, "construction_usd": 0.1, "solver_usd": 0.35,
             "judge_usd": 0.1, "by_model": {"vendor/alpha": {"solve_usd": 0.35, "judge_usd": 0.1,
                                                            "total_usd": 0.45}}},
        ],
    },
    "cases_detail": [
        {
            "case_name": "c2", "case_index": 2, "case_title": "Gadget Inc: Pricing",
            "fictional_case": False,
            "questions": [
                {
                    "question": "Recommend a price.", "solution": "$40.",
                    "grading_rubric": ["States a number.", "Justifies it."],
                    "discipline": "Strategy", "numerical": False, "subjective": True,
                    "answers": [
                        {"model": "vendor/alpha", "answer": "Price at $40.", "graded_score": 2,
                         "n_criteria": 2, "standard_score": 1.0, "complete_answer": 1,
                         "reasoning": "ok"},
                    ],
                }
            ],
        },
        {
            "case_name": "c1", "case_index": 1, "case_title": "Widget Co: Entry",
            "fictional_case": True,
            "questions": [
                {
                    "question": "Compute break-even.", "solution": "12,000 units.",
                    "grading_rubric": ["Answer computes CM.", "Answer computes BE as 12,000."],
                    "discipline": "Finance", "numerical": True, "subjective": False,
                    "intermediate_work_activity": "Analyze business or financial data.",
                    "answers": [
                        {"model": "vendor/alpha", "answer": "<b>break-even</b> is 12,000",
                         "graded_score": 2, "n_criteria": 2, "standard_score": 1.0,
                         "complete_answer": 1, "reasoning": "both hit"},
                        {"model": "<script>x</script>", "answer": "", "graded_score": 0,
                         "n_criteria": 2, "standard_score": 0.0, "complete_answer": 0,
                         "reasoning": ""},
                    ],
                }
            ],
        }
    ],
}

page = html_report.render(REPORT)

check("renders a non-trivial page", len(page) > 5000, f"{len(page)} chars")
# No resource-loading references to remote hosts: a <script src>, <img src>, or
# <link href> would fetch at render time. An <a href> is a navigation link the user
# clicks, not a request, so it is allowed (the footer links to the source repo).
check("no external resource requests",
      not re.search(r'src=["\']https?:', page)
      and not re.search(r'<link[^>]+href=["\']https?:', page))
check("only anchor tags reference remote hosts",
      all(re.search(r'<a\b', page[max(0, m.start() - 60):m.start()])
          for m in re.finditer(r'href=["\']https?:', page)))
check("no remote @import or url()", "@import" not in page and "url(http" not in page)

# 2 models x (1 standard + 1 complete + 2 strata + 2 disciplines) = 12 score bars
# (by-case is line charts now, not bars)
check("one bar per model per category", page.count('class="row"') == 12,
      f'{page.count(chr(34)+"row"+chr(34))} rows')
check("every bar has a tooltip", page.count("data-tip=") == page.count('class="row"'))
check("legend present for series", page.count('class="legend"') >= 1)
check("table view ships (relief for the low-contrast hue)", "<table>" in page)

# A model name is user-controlled config; it must never become markup.
check("model names are escaped", "<script>alert(1)</script>" not in page)
check("escaped form is present instead", "&lt;script&gt;" in page)

check("light is the default (OS dark does not flip it)",
      "prefers-color-scheme" not in page)
check("a dark variant exists behind an explicit opt-in",
      '[data-theme="dark"]' in page)
check("both light and dark series hues present",
      html_report.SERIES_LIGHT[0] in page and html_report.SERIES_DARK[0] in page)
check("editorial chrome present (serif headings + teal accent + stat tiles)",
      "--serif" in page and "#146074" in page and 'class="stat"' in page)

widths = [float(w) for w in re.findall(r"width:([\d.]+)%", page)]
check("bar widths within the track", widths and all(0 <= w <= 100 for w in widths))
cis = [(float(a), float(b)) for a, b in re.findall(r"left:([\d.]+)%;right:([\d.]+)%", page)]
check("CI bounds never invert", all(a + b <= 100.01 for a, b in cis), f"{len(cis)} intervals")

check("source repo is linked", 'href="https://github.com/harrywang/obcb"' in page)
check("run configuration is shown", "bootstrap" in page and "min rubric criteria" in page)

# --- by-case section: line charts (x = case, y = score / cost), no filter ---
check("by-case section present", "<h2>By case</h2>" in page)
check("score-by-case line chart present",
      "Standard score by case" in page and page.count('class="linechart"') >= 2)
check("cost-by-case line chart present",
      "Cost by case" in page and "polyline" in page)
check("cost lines cover total + the three-way split",
      "Total" in page and "Construction" in page and "Solver" in page and "Judge" in page)
check("both cases appear on the x-axis", "Widget Co: Entry" in page and "Gadget Inc: Pricing" in page)
check("no case filter anywhere (removed)",
      "case-filter" not in page and "casefilter" not in page)
check("x-axis is numbered by case-list index, not titles",
      'class="axnum"' in page and ">1</text>" in page and ">2</text>" in page)
check("a number -> title key is shown under the charts",
      'class="casekey"' in page and 'class="knum"' in page)
check("detail cards carry the same case number", 'class="cnum"' in page)
check("line-chart point tooltips escape untrusted model names",
      "&lt;script&gt;alert(1)&lt;/script&gt;" in page)

# --- case & question detail (the builder's output made inspectable) ---
check("detail section present", "Case &amp; question detail" in page)
check("reference solution shown", "Reference solution" in page and "12,000 units." in page)
check("rubric criteria shown", "Answer computes BE as 12,000." in page)
check("per-model score shown in detail", "2/2" in page)
check("case title is a collapsible summary", 'details class="case"' in page)
check("model answer is escaped inside the detail too",
      "&lt;b&gt;break-even&lt;/b&gt;" in page and "<b>break-even</b>" not in page)

out = Path(tempfile.mkdtemp(prefix="obcb-html-")) / "r.html"
html_report.write(REPORT, out)
written = out.read_text(encoding="utf-8")
check("write() emits a full document",
      written.startswith("<!doctype html>") and written.rstrip().endswith("</html>"))

print()
sys.exit(0 if ok else 1)
