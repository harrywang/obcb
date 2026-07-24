"""End-to-end cost-accounting check against a fake OpenAI-compatible server.

Runs a real HTTP server that returns OpenRouter-shaped responses including
usage.cost, so the whole path is exercised: request -> usage parse -> ledger ->
cache write -> cache hit -> saved-cost accounting -> usage.jsonl -> report.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8931
TMP = tempfile.mkdtemp(prefix="obcb-cost-")
os.environ["OBCB_OUT_DIR"] = TMP
os.environ["OPENROUTER_API_KEY"] = "fake"
os.environ["OPENROUTER_BASE_URL"] = f"http://127.0.0.1:{PORT}/v1"
os.environ["OBCB_CONCURRENCY"] = "4"

CALLS = {"n": 0}
COST_PER_CALL = 0.0025


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][0]["content"]
        CALLS["n"] += 1

        if "cataloguing a business school teaching case" in prompt:
            content = json.dumps({
                "case_title": "T", "case_summary": "S",
                "case_learning_objectives": ["a"], "fictional_case": True,
                "fictional_reasoning": "r",
            })
        elif "recover those question/answer pairs" in prompt:
            content = json.dumps([{
                "question": "Compute the break-even volume in units for this case.",
                "solution": "Break-even is 12,000 units at a $10 contribution margin.",
                "task_description": "Compute break-even.",
            }])
        elif "writing a grading rubric" in prompt:
            content = json.dumps(["Answer computes CM as $10.", "Answer computes BE as 12,000."])
        elif "annotating one question" in prompt:
            content = json.dumps({
                "discipline": "Finance", "numerical": True, "primarily_numerical": True,
                "subjective": False, "subjective_reasoning": "x",
                "intermediate_work_activity_id": "4.A.2.a.4.I11",
            })
        elif "Detailed Work Activity" in prompt:
            content = json.dumps({"detailed_work_activity_id": "4.A.2.a.4.I11.D06"})
        elif "You are given a business case" in prompt:
            content = "Model answer."
        else:
            content = "Reasoning. <<2>>"

        payload = {
            "id": "gen-1",
            "choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": "stop", "index": 0}],
            "model": body["model"],
            "object": "chat.completion",
            "created": 0,
            # OpenRouter shape: cost is inline on usage
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                      "cost": COST_PER_CALL},
        }
        out = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


server = HTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

from obcb import build, config, evaluate, report, usage  # noqa: E402

src = config.DATA_DIR / "outputs" / "cases_raw.jsonl"
if not src.exists():
    sys.exit("Run `uv run obcb extract` first.")
shutil.copy(src, config.CASES_RAW)

build.run(limit=2)
build_calls = CALLS["n"]
build_cost = usage.LEDGER.totals().cost_usd
usage.LEDGER.save(config.USAGE_LOG, "build")
usage.LEDGER = usage.Ledger()

evaluate.run(models=["stub/a", "stub/b"])
eval_cost = usage.LEDGER.totals().cost_usd
eval_models = set(usage.LEDGER.by(0))
eval_stages = set(usage.LEDGER.by(1))
usage.LEDGER.save(config.USAGE_LOG, "evaluate")
usage.LEDGER = usage.Ledger()

# Plain re-run: every question is already in the results, so evaluate SKIPS all of them
# and makes no calls at all (not even cache hits) — the ledger stays empty.
calls_before = CALLS["n"]
evaluate.run(models=["stub/a", "stub/b"])
skip_rerun = usage.LEDGER.totals()
skip_calls_made = CALLS["n"] - calls_before
skip_rerun_empty = usage.LEDGER.is_empty()
usage.LEDGER.save(config.USAGE_LOG, "evaluate")  # empty ledger: save is a no-op
usage.LEDGER = usage.Ledger()

# Forced re-run: reprocesses every question, but the response cache serves each one, so it
# issues zero HTTP calls and bills $0 while reporting the savings.
calls_before_forced = CALLS["n"]
evaluate.run(models=["stub/a", "stub/b"], force=True)
forced = usage.LEDGER.totals()
forced_calls_made = CALLS["n"] - calls_before_forced
usage.LEDGER.save(config.USAGE_LOG, "evaluate")
usage.LEDGER = usage.Ledger()

rep = report.run()
server.shutdown()

print("\n===== assertions =====")
ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")


check("build cost == calls x unit cost",
      abs(build_cost - build_calls * COST_PER_CALL) < 1e-9,
      f"${build_cost:.4f} over {build_calls} calls")
check("evaluate cost non-zero", eval_cost > 0, f"${eval_cost:.4f}")
check("both solver models tracked separately",
      {"stub/a", "stub/b"}.issubset(eval_models), str(sorted(eval_models)))
# The judge's grading spend is booked to the solver it graded (record_as), so the judge
# model id does NOT appear as its own line — this is what keeps grade out of construction
# even when the judge shares the annotator's model id (it does here, by default).
check("judge grading is attributed to the solvers, not the judge model id",
      config.JUDGE_MODEL == config.ANNOTATOR_MODEL  # precondition: they collide by default
      and eval_models == {"stub/a", "stub/b"}, str(sorted(eval_models)))
check("stages labelled", {"solving", "grading"}.issubset(eval_stages), str(sorted(eval_stages)))
# Incremental skip: a plain re-run does no work at all (short-circuits before the cache).
check("plain re-run makes zero calls (skipped, not re-run)", skip_calls_made == 0,
      f"{skip_calls_made} new calls")
check("plain re-run records nothing (empty ledger)", skip_rerun_empty and skip_rerun.calls == 0)
# Forced re-run: reprocesses, but the cache serves every call — no HTTP, no bill, savings shown.
check("forced re-run makes no HTTP calls (cache serves)", forced_calls_made == 0,
      f"{forced_calls_made} new")
check("forced re-run billed nothing", forced.cost_usd == 0.0, f"${forced.cost_usd:.4f}")
check("forced re-run reports cache savings", forced.saved_usd > 0, f"${forced.saved_usd:.4f} saved")
check("forced re-run counted all calls as cached", forced.cached_calls == forced.calls)
check("forced re-run token counts recorded",
      forced.prompt_tokens > 0 and forced.completion_tokens > 0,
      f"{forced.prompt_tokens} prompt / {forced.completion_tokens} output")

log = [json.loads(x) for x in config.USAGE_LOG.read_text().splitlines() if x]
# build + evaluate + forced-evaluate = 3 records; the skipped re-run had an empty ledger,
# so its save() was a no-op and it adds no record.
check("usage.jsonl has one record per non-empty run", len(log) == 3, f"{len(log)} records")
check("log records have timestamp + command",
      all("timestamp" in r and "command" in r for r in log))
check("report includes cost", "cost" in rep and rep["cost"]["total_usd"] > 0,
      f"${rep.get('cost', {}).get('total_usd', 0):.4f}")
check("report.md has a Cost section", "## Cost" in config.REPORT_MD.read_text())

# --- three-way split: construction + solver + judge ---
cost = rep["cost"]
check("cost carries the three category totals",
      {"construction_usd", "solver_usd", "judge_usd"} <= set(cost))
check("construction == the build cost (no eval leaked in)",
      abs(cost["construction_usd"] - build_cost) < 1e-9,
      f'construction ${cost["construction_usd"]:.4f} vs build ${build_cost:.4f}')
check("solver + judge == the evaluate cost",
      abs(cost["solver_usd"] + cost["judge_usd"] - eval_cost) < 1e-9,
      f'solver ${cost["solver_usd"]:.4f} + judge ${cost["judge_usd"]:.4f} vs eval ${eval_cost:.4f}')
check("judge cost is non-zero and NOT folded into construction",
      cost["judge_usd"] > 0, f'judge ${cost["judge_usd"]:.4f}')
check("construction + solver + judge == total",
      abs(cost["construction_usd"] + cost["solver_usd"] + cost["judge_usd"] - cost["total_usd"]) < 1e-9)
# Both solvers carry a solve and a judge line. (stub/b's grade is $0 here: both mock
# solvers return the same answer, so its grade prompt is a cache hit — still booked to
# stub/b, just at zero cost. The judge total being non-zero is asserted above.)
check("per-model split reports each solver's solve and judge",
      set(cost["by_model_eval"]) >= {"stub/a", "stub/b"}
      and all("solve_usd" in v and "judge_usd" in v for v in cost["by_model_eval"].values())
      and all(v["solve_usd"] > 0 for v in cost["by_model_eval"].values()))

# --- per-case (per-paper) cost ---
per_case = cost.get("per_case", [])
check("report has per-case cost", len(per_case) >= 1, f"{len(per_case)} cases")
if per_case:
    c = per_case[0]
    check("each case splits into construction + solver + judge + per-model",
          {"case", "total_usd", "construction_usd", "solver_usd", "judge_usd", "by_model"} <= set(c))
    check("per-model column holds the solvers", {"stub/a", "stub/b"} <= set(c["by_model"]))
    check("construction models are not counted as solvers",
          config.BUILDER_MODEL not in c["by_model"]
          and config.ANNOTATOR_MODEL not in c["by_model"])
    check("case total == construction + solver + judge",
          abs(c["total_usd"] - (c["construction_usd"] + c["solver_usd"] + c["judge_usd"])) < 1e-9,
          f'total {c["total_usd"]:.4f} vs '
          f'{c["construction_usd"] + c["solver_usd"] + c["judge_usd"]:.4f}')
check("usage records carry by_case", all("by_case" in r for r in log))

# --- timing: per-call model time rolls up per case, plus wall clock per run ---
check("every run record carries wall-clock elapsed_s", all("elapsed_s" in r for r in log))
check("billed calls recorded model time", any(r["total"].get("seconds", 0) > 0 for r in log))
check("per-case time is reported", all("seconds" in c for c in per_case))
check("report carries wall and model time",
      "wall_seconds" in cost and "model_seconds" in cost and cost["model_seconds"] > 0)
check("report.md shows a Time line", "- Time:" in config.REPORT_MD.read_text())

total_logged = sum(r["total"]["cost_usd"] for r in log)
check("logged total == build + evaluate",
      abs(total_logged - (build_cost + eval_cost)) < 1e-9, f"${total_logged:.4f}")

print(f"\nHTTP calls served: {CALLS['n']}   outputs: {TMP}")
sys.exit(0 if ok else 1)
