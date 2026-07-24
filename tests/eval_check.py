"""Assert evaluate does not grade a failed solve — the judge is skipped, no wasted call.

A solver whose answer comes back as the FAILED sentinel scores 0 directly; sending that
sentinel to the judge would burn a paid grading call on a non-answer.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

os.environ["OBCB_OUT_DIR"] = tempfile.mkdtemp(prefix="obcb-eval-")
os.environ["OPENROUTER_API_KEY"] = "x"

from obcb import config, evaluate, llm  # noqa: E402

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    ok = ok and cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")


# Three questions in one case; middle solve will fail.
bench = [
    {
        "case_name": "c1", "case_title": "C", "case_summary": "s", "fictional_case": True,
        "question": f"Q{i}", "solution": "S", "grading_rubric": ["a", "b"],
        "discipline": "Finance", "numerical": True, "subjective": False,
    }
    for i in range(3)
]
config.BENCHMARK.parent.mkdir(parents=True, exist_ok=True)
config.BENCHMARK.write_text("\n".join(json.dumps(b) for b in bench), encoding="utf-8")
config.CASES.write_text(
    json.dumps({"case_name": "c1", "case_summary": "s", "case_clean_text": "the case text"}),
    encoding="utf-8",
)

grading_batch_sizes = []


def stub_map(self, prompts, desc="prompting", cases=None, record_as=None):
    if desc == "solving":
        # answer 1 (index 1) fails; the other two succeed
        return ["answer to Q0", llm.FAILED + "boom", "answer to Q2"]
    if desc == "grading":
        grading_batch_sizes.append(len(prompts))
        return ["reasoning <<2>>"] * len(prompts)
    return ["x"] * len(prompts)


llm.LLM.map = stub_map  # type: ignore[method-assign]

res = evaluate.run(models=["solver/x"])
rows = res["solver/x"]

check("all 3 questions produce a row", len(rows) == 3, f"{len(rows)} rows")
check(
    "judge graded only the 2 non-failed answers (skipped the failed one)",
    grading_batch_sizes == [2],
    f"grading batch sizes {grading_batch_sizes}",
)

failed_rows = [r for r in rows if r["model_answer_failed"]]
check("exactly one row is marked as a failed solve", len(failed_rows) == 1)
check(
    "the failed solve scores 0 without a judge call",
    failed_rows[0]["graded_score"] == 0 and failed_rows[0]["standard_score"] == 0.0,
)
check(
    "non-failed rows carry the judge's score",
    all(r["graded_score"] == 2 for r in rows if not r["model_answer_failed"]),
)

print()
sys.exit(0 if ok else 1)
