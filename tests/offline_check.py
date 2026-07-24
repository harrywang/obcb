"""Offline smoke test: build -> evaluate -> report with the LLM stubbed out.

Run with:  uv run python tests/offline_check.py   (no API key needed)

Verifies plumbing only: JSON parsing, the rubric quality gate, O*NET resolution,
score clamping/normalisation, and report aggregation. No network calls.
"""

import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="obcb-offline-")
os.environ["OBCB_OUT_DIR"] = TMP
os.environ["OPENROUTER_API_KEY"] = "offline-stub"

from obcb import build, config, evaluate, llm, onet, report  # noqa: E402

# Synthesise the input rather than reading whatever corpus happens to be on disk: the
# assertions below depend on how many questions exist, and the real corpus size changes
# with the --limit the user last fetched with.
N_CASES = 4
config.CASES_RAW.parent.mkdir(parents=True, exist_ok=True)
config.CASES_RAW.write_text(
    "\n".join(
        json.dumps({
            "case_name": f"synthetic-case-{i}",
            "case_clean_text": f"Case {i}: a firm weighs a decision. " * 40,
            "instructor_clean_text": f"Teaching note {i}. Suggested answer: do X. " * 40,
        })
        for i in range(N_CASES)
    ) + "\n",
    encoding="utf-8",
)

TAX = onet.load()
GOOD_IWA = "4.A.2.a.4.I11"
GOOD_DWA = TAX.dwas_for_iwa[GOOD_IWA][0]


def stub(self, prompts, desc="prompting", cases=None, record_as=None):
    out = []
    for i, p in enumerate(prompts):
        if "cataloguing a business school teaching case" in p:
            out.append(json.dumps({
                "case_title": "Stub Case",
                "case_summary": "A stub summary used for offline plumbing checks.",
                "case_learning_objectives": ["Objective A", "Objective B"],
                "fictional_case": i % 2 == 0,
                "fictional_reasoning": "stub",
            }))
        elif "recover those question/answer pairs" in p:
            # Two valid questions, one that must be dropped for a too-short solution.
            out.append("```json\n" + json.dumps([
                {"question": "Q" + str(i) + "a: compute the break-even volume in units.",
                 "solution": "Break-even is 12,000 units at a $10 contribution margin.",
                 "task_description": "Compute break-even volume."},
                {"question": "Q" + str(i) + "b: recommend a channel and justify the choice.",
                 "solution": "Prefer online-only; lower fixed costs reach profitability sooner.",
                 "task_description": "Recommend a channel."},
                {"question": "Q" + str(i) + "c: unanswered question with no solution.",
                 "solution": "n/a", "task_description": "drop me"},
            ]) + "\n```")
        elif "writing a grading rubric" in p:
            # One rubric comes back unusable, to prove the gate drops it.
            out.append("[]" if i == 0 else json.dumps([
                "Answer computes contribution margin per unit as $10.",
                "Answer computes break-even units as 12,000.",
                "Answer states a clear recommendation.",
            ]))
        elif "annotating one question" in p:
            out.append("Here you go:\n" + json.dumps({
                "discipline": "Finance" if i % 2 else "Not A Real Discipline",
                "numerical": i % 2 == 0,
                "primarily_numerical": True,
                "subjective": i % 2 == 1,
                "subjective_reasoning": "stub",
                "intermediate_work_activity_id": GOOD_IWA if i % 3 else "bogus.id",
            }))
        elif "Detailed Work Activity" in p:
            out.append(json.dumps({"detailed_work_activity_id": GOOD_DWA}))
        elif "You are given a business case and a question" in p:
            out.append("Stub model answer with reasoning.")
        elif "experienced judge" in p:
            # Full marks, partial, over-cap (must clamp), and an unparseable grading.
            out.append(["<<3>>", "<<1>>", "<<99>>", "no score here"][i % 4])
        else:
            raise AssertionError("unrecognised prompt:\n" + p[:200])
    return out


llm.LLM.map = stub

build.run()
evaluate.run(models=["stub/model-a", "stub/model-b"])
rep = report.run()

bench = [json.loads(x) for x in open(config.BENCHMARK)]
res = [json.loads(x) for x in open(config.RESULTS_DIR / "stub_model-a.jsonl")]

print("\n===== assertions =====")
ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")


check("unanswered question dropped", all("unanswered" not in b["question"] for b in bench))
check("empty rubric dropped", all(len(b["grading_rubric"]) >= 2 for b in bench))
check("invalid discipline coerced to Other",
      all(b["discipline"] in config.DISCIPLINES for b in bench),
      f"disciplines={sorted({b['discipline'] for b in bench})}")
check("bogus IWA id nulled out",
      all(b["intermediate_work_activity_id"] in (None, GOOD_IWA) for b in bench))
check("valid IWA resolved to parent WA",
      all(b["work_activity_id"] == "4.A.2.a.4"
          for b in bench if b["intermediate_work_activity_id"] == GOOD_IWA))
check("DWA resolved under its IWA",
      all(b["detailed_work_activity_id"] in (None, GOOD_DWA) for b in bench))
check("primarily_numerical implies numerical",
      all(b["numerical"] or not b["primarily_numerical"] for b in bench))
check("score clamped to rubric length",
      all(r["graded_score"] <= len(r["grading_rubric"]) for r in res),
      f"max={max(r['graded_score'] for r in res)}")
check("standard_score in [0,1]", all(0.0 <= r["standard_score"] <= 1.0 for r in res))
check("complete_answer iff full marks",
      all(r["complete_answer"] == int(r["graded_score"] == len(r["grading_rubric"])) for r in res))
check("unparseable grading scores 0",
      any(r["graded_score"] == 0 for r in res))
check("report covers both models", set(rep["models"]) == {"stub/model-a", "stub/model-b"})
check("report.md written", config.REPORT_MD.exists() and config.REPORT_MD.stat().st_size > 0)
check("scores.json written", config.SCORES_JSON.exists())
check("strata present",
      set(rep["models"]["stub/model-a"]["by_question_type"]) ==
      {"numerical", "non_numerical", "subjective", "objective", "fictional_case", "real_case"})

print(f"\n{len(bench)} benchmark questions built from {N_CASES} synthetic cases")
print("outputs in", TMP)
sys.exit(0 if ok else 1)
