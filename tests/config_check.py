"""Assert every hoisted knob is honoured from the env and reaches the manifest."""
import json
import os
import sys
import tempfile
os.environ.update({
    "OBCB_OUT_DIR": tempfile.mkdtemp(prefix="obcb-cfg-"),
    "OPENROUTER_API_KEY": "fake",
    "OBCB_TEMPERATURE": "0.7", "OBCB_SOLVER_MAX_TOKENS": "123",
    "OBCB_JUDGE_MAX_TOKENS": "456", "OBCB_BUILDER_MAX_TOKENS": "789",
    "OBCB_ANNOTATOR_MAX_TOKENS": "321", "OBCB_MIN_RUBRIC_CRITERIA": "5",
    "OBCB_MIN_QUESTION_CHARS": "99", "OBCB_BOOTSTRAP_B": "77",
    "OBCB_BOOTSTRAP_SEED": "42", "OBCB_KEEP_PICTURE_TEXT": "yes",
    "OBCB_DOCLING_OCR": "1", "OBCB_EXTRACTOR": "pypdf",
})
from obcb import config, evaluate, llm  # noqa: E402

ok = True
def check(label, cond, detail=""):
    global ok
    ok = ok and cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  '+detail if detail else ''}")

r = config.resolved()
check("temperature from env", config.TEMPERATURE == 0.7)
check("solver max_tokens from env", config.SOLVER_MAX_TOKENS == 123)
check("rubric gate from env", config.MIN_RUBRIC_CRITERIA == 5)
check("bootstrap B/seed from env", (config.BOOTSTRAP_B, config.BOOTSTRAP_SEED) == (77, 42))
check("bool env parses 'yes' and '1'", config.KEEP_PICTURE_TEXT and config.DOCLING_OCR)
check("manifest reflects overrides",
      r["sampling"]["solver_max_tokens"] == 123 and r["reporting"]["bootstrap_b"] == 77)

# LLMParams defaults must pick up the env temperature, not a stale literal
check("LLMParams default temperature tracks config", config.LLMParams().temperature == 0.7)

# The real call sites must use the configured budgets
solver = llm.LLM("x/y", params=config.LLMParams(max_tokens=config.SOLVER_MAX_TOKENS))
check("solver LLM built with configured budget", solver.params.max_tokens == 123)
check("solver LLM built with configured temperature", solver.params.temperature == 0.7)

# bootstrap honours config without an explicit arg
from obcb.report import bootstrap_ci  # noqa: E402
lo, hi = bootstrap_ci([0.0, 1.0] * 10)
check("bootstrap_ci runs off config", isinstance(lo, float) and lo < hi)

# extractors read config, not os.environ directly
import inspect  # noqa: E402
from obcb import extractors  # noqa: E402
src = inspect.getsource(extractors)
check("no stray OBCB_ env reads in extractors",
      "OBCB_KEEP_PICTURE_TEXT" not in src and "OBCB_DOCLING_OCR" not in src)
src_all = "".join(inspect.getsource(m) for m in (evaluate, extractors))
check("no magic max_tokens literals at call sites",
      "max_tokens=10000" not in src_all)

p = config.save_run_config("test")
saved = json.loads(p.read_text())
check("manifest written with version + timestamp",
      "obcb_version" in saved and "timestamp" in saved)
check("manifest matches resolved()", saved["config"] == r)
sys.exit(0 if ok else 1)
