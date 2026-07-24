"""Incremental processing: a case already in a stage's output is skipped, not re-run.

This is the property the response cache alone did not give. The cache made a re-run *free*
(cache hits bill nothing), but it still walked every case through every stage. Here we prove
the stronger guarantee: a fully-processed case makes ZERO LLM calls on a re-run — build and
evaluate short-circuit before the cache — while a brand-new case in the same corpus is still
processed, and ``--force`` reprocesses everything.

Run with:  uv run python tests/skip_check.py   (no API key needed)
"""

import json
import os
import sys
import tempfile

os.environ["OBCB_OUT_DIR"] = tempfile.mkdtemp(prefix="obcb-skip-")
os.environ["OPENROUTER_API_KEY"] = "offline-stub"

from obcb import build, config, evaluate, llm, onet  # noqa: E402

TAX = onet.load()
GOOD_IWA = "4.A.2.a.4.I11"
GOOD_DWA = TAX.dwas_for_iwa[GOOD_IWA][0]

# One valid question per case, one usable rubric — so each case yields exactly one
# benchmark question and per-case call counts stay constant and easy to reason about.
CALLS = {"n": 0}


def stub(self, prompts, desc="prompting", cases=None, record_as=None):
    CALLS["n"] += len(prompts)
    out = []
    for p in prompts:
        if "cataloguing a business school teaching case" in p:
            out.append(json.dumps({
                "case_title": "Stub", "case_summary": "s",
                "case_learning_objectives": ["a"], "fictional_case": False,
                "fictional_reasoning": "r",
            }))
        elif "recover those question/answer pairs" in p:
            out.append(json.dumps([{
                "question": "Compute the break-even volume in units for this case.",
                "solution": "Break-even is 12,000 units at a $10 contribution margin.",
                "task_description": "Compute break-even.",
            }]))
        elif "writing a grading rubric" in p:
            out.append(json.dumps(["Computes CM as $10.", "Computes BE as 12,000."]))
        elif "annotating one question" in p:
            out.append(json.dumps({
                "discipline": "Finance", "numerical": True, "primarily_numerical": True,
                "subjective": False, "subjective_reasoning": "x",
                "intermediate_work_activity_id": GOOD_IWA,
            }))
        elif "Detailed Work Activity" in p:
            out.append(json.dumps({"detailed_work_activity_id": GOOD_DWA}))
        elif "You are given a business case and a question" in p:
            out.append("A stub model answer with enough length to clear the gate.")
        elif "experienced judge" in p:
            out.append("Reasoning. <<2>>")
        else:
            raise AssertionError("unrecognised prompt:\n" + p[:160])
    return out


llm.LLM.map = stub

NC = 3
config.CASES_RAW.parent.mkdir(parents=True, exist_ok=True)
config.CASES_RAW.write_text(
    "\n".join(
        json.dumps({
            "case_name": f"c{i}",
            "case_clean_text": f"Case {i}: a firm weighs a decision. " * 30,
            "instructor_clean_text": f"Note {i}. Suggested answer: do X. " * 30,
        })
        for i in range(NC)
    ) + "\n",
    encoding="utf-8",
)


def delta():
    """Calls made since the last delta() call."""
    n = CALLS["n"] - delta.mark
    delta.mark = CALLS["n"]
    return n


delta.mark = 0


def names(path):
    return [json.loads(x)["case_name"] for x in path.read_text().splitlines() if x]


print("===== assertions =====")
ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")


# --- build: first case ---
build.run(limit=1)
per_case = delta()
c0_row = [json.loads(x) for x in config.BENCHMARK.read_text().splitlines() if x]
check("first build processes one case", per_case > 0 and names(config.CASES) == ["c0"])
check("first build wrote one benchmark question", len(c0_row) == 1)

# --- build: two more cases, first is skipped ---
build.run(limit=3)
d = delta()
check("second build skips the done case, processes only the 2 new ones",
      d == 2 * per_case, f"{d} calls vs {2 * per_case} expected for 2 cases")
check("cases.jsonl now holds all three, in order", names(config.CASES) == ["c0", "c1", "c2"])
bench = [json.loads(x) for x in config.BENCHMARK.read_text().splitlines() if x]
check("benchmark merged to three questions", len(bench) == 3)
check("the already-built case's row was left untouched",
      next(b for b in bench if b["case_name"] == "c0") == c0_row[0])

# --- build: nothing new → zero calls ---
build.run(limit=3)
check("third build touches nothing (all already built)", delta() == 0)

# --- build --force: everything reprocessed ---
build.run(limit=3, force=True)
forced = delta()
check("forced build reprocesses all three cases", forced == 3 * per_case,
      f"{forced} calls vs {3 * per_case} expected")
check("forced build keeps three cases (no duplication)",
      names(config.CASES) == ["c0", "c1", "c2"]
      and len([x for x in config.BENCHMARK.read_text().splitlines() if x]) == 3)

# --- evaluate: first model over all three questions ---
evaluate.run(models=["m/x"])
e_first = delta()
res_path = config.RESULTS_DIR / "m_x.jsonl"
check("evaluate scores all three questions", e_first > 0
      and len([x for x in res_path.read_text().splitlines() if x]) == 3)

# --- evaluate again: all questions already done → skipped ---
evaluate.run(models=["m/x"])
check("re-evaluate skips every already-scored question", delta() == 0)

# --- evaluate --force: redo all ---
evaluate.run(models=["m/x"], force=True)
check("forced evaluate re-scores all three", delta() == e_first)

# --- a new model still evaluates everything (its own results file is empty) ---
evaluate.run(models=["m/y"])
check("a newly added model evaluates every question", delta() == e_first)
check("m/x results untouched by m/y run",
      len([x for x in res_path.read_text().splitlines() if x]) == 3)

# --- partial: a new question appears; only it is evaluated ---
config.CASES_RAW.write_text(
    config.CASES_RAW.read_text()
    + json.dumps({
        "case_name": "c3",
        "case_clean_text": "Case 3: a firm weighs a decision. " * 30,
        "instructor_clean_text": "Note 3. Suggested answer: do X. " * 30,
    }) + "\n",
    encoding="utf-8",
)
build.run(limit=4)
delta()  # ignore the build cost for c3
evaluate.run(models=["m/x"])
check("evaluate picks up only the one newly built question",
      delta() == e_first // 3, f"expected {e_first // 3} (solve+grade for 1 question)")
check("m/x now covers four questions",
      len([x for x in res_path.read_text().splitlines() if x]) == 4)

print()
sys.exit(0 if ok else 1)
