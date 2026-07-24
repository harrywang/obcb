"""Assert the candidate detector, without touching the network.

`find-cases` decides where the case ends and the instructor note begins. Getting that
boundary wrong in one direction truncates the case; in the other it hands the solver its
own answer key. `analyze` is pure logic over page texts, so both directions can be
exercised directly, along with the rejections that keep bad material out of the corpus.
"""

from __future__ import annotations

import sys

from obcb import discover

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    ok = ok and cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")


HEADER = "Journal of Business Cases and Applications        Volume 99"
NARRATIVE = HEADER + "\n" + "\n".join(
    f"Line {i} of the case narrative describing the firm and the decision it faces."
    for i in range(12)
)
NOTE = "TEACHING NOTES\n1. What should the firm do?\nSuggested Answer: raise price to $24."
# A note whose first page is preamble, with the answers starting further in. Only this
# shape may share its heading page with the case half.
NOTE_PREAMBLE = "TEACHING NOTES\nThis case suits an introductory strategy course.\n" + (
    "It works well in a 90 minute session with teams of four. " * 6
)
LATER_ANSWERS = "1. What should the firm do?\nSuggested Answer: raise price to $24."

# --- clean split: the heading opens its own page --------------------------------------

v = discover.analyze([NARRATIVE] * 5 + [NOTE, "further discussion"])
check("clean split detected", v["ok"], v.get("reason", ""))
check("case half ends before the heading page", v.get("case_pages") == [1, 5], str(v.get("case_pages")))
check("instructor half starts at the heading page", v.get("instructor_pages") == [6, 7])
check("clean split reports no overlap", v.get("overlap") is False)

# --- shared boundary page: narrative tail and heading on the same page ----------------

v = discover.analyze([NARRATIVE] * 4 + [NARRATIVE + "\n" + NOTE_PREAMBLE, LATER_ANSWERS])
check("shared boundary page flagged as overlap", v["ok"] and v.get("overlap") is True, v.get("reason", ""))
check(
    "shared page lands in both halves",
    v.get("case_pages") == [1, 5] and v.get("instructor_pages") == [5, 6],
    f"{v.get('case_pages')} / {v.get('instructor_pages')}",
)

# ...but never when the answers start on that same page. Handing it to the case half would
# put the answer key in front of the solver, which is worse than a clipped last paragraph.
v = discover.analyze([NARRATIVE] * 4 + [NARRATIVE + "\n" + NOTE, "more notes"])
check(
    "heading page with answers on it is NOT shared",
    v["ok"] and v.get("overlap") is False and v.get("case_pages") == [1, 4],
    f"{v.get('case_pages')} / overlap={v.get('overlap')}",
)

# An answers-style heading is itself the start of the answers: "Suggested Answers to Case
# Question 1" both opens the note and begins answering, so its page can never be shared.
v = discover.analyze(
    [NARRATIVE] * 4
    + [NARRATIVE + "\nSuggested Answers to Case Question 1\nThe fraud triangle has three parts."]
    + ["more answers"]
)
check(
    "answers-style heading is never shared",
    v["ok"] and v.get("overlap") is False and v.get("case_pages") == [1, 4],
    f"{v.get('case_pages')} / overlap={v.get('overlap')}",
)

# A page carrying only the running header before the heading is NOT an overlap.
v = discover.analyze([NARRATIVE] * 5 + [HEADER + "\n" + NOTE, "x"])
check("running header alone does not count as narrative", v["ok"] and v.get("overlap") is False)

# --- rejections -----------------------------------------------------------------------

for label, pages, needle in [
    ("no teaching note", [NARRATIVE] * 6, "no teaching-note heading"),
    (
        "questions posed but never answered",
        [NARRATIVE] * 5 + ["TEACHING NOTES\n1. What should the firm do?\n2. Why?"],
        "never answers",
    ),
    ("note starts on page 1", [NOTE] + [NARRATIVE] * 5, "too little case"),
    ("note starts too early", [NARRATIVE, NARRATIVE, NOTE, NARRATIVE], "too little case"),
]:
    v = discover.analyze(pages)
    check(f"rejected: {label}", not v["ok"] and needle in v["reason"], v.get("reason", ""))

# --- a title mentioning the note must not be mistaken for the heading -----------------

# Title page + 4 narrative pages = case pp.1-5; the real heading opens page 6. An
# unanchored heading match would fire on page 1 and swallow the entire case.
v = discover.analyze(
    ["A Case and Teaching Notes: the firm decides"] + [NARRATIVE] * 4 + [NOTE, "x"]
)
check(
    "title mentioning 'Teaching Notes' does not trigger the split",
    v["ok"] and v.get("case_pages") == [1, 5] and v.get("instructor_pages") == [6, 7],
    f"{v.get('case_pages')} / {v.get('instructor_pages')}",
)

# --- answer-label variants -------------------------------------------------------------

for label, note in [
    ("bullet-prefixed 'Answer:'", "TEACHING NOTES\n1. Q\n• Answer: because of X."),
    ("numbered 'Answer:'", "TEACHING NOTES\n1. Answer: because of X."),
    ("'Sample Response'", "TEACHING NOTES\nSample Response: because of X."),
    ("'Instructor Key'", "TEACHING NOTES\nInstructor Key to Case Questions\n1. X because Y."),
    ("'Answers to discussion'", "TEACHING NOTES\nAnswers to discussion questions\n1. X."),
]:
    check(f"recognised: {label}", discover.analyze([NARRATIVE] * 5 + [note, "x"])["ok"])

# --- slugs and corrupt input ------------------------------------------------------------

slug = discover.slugify("Guns or Money? A Banking Dilemma")
check(
    "slugify is filename-safe and prefixed",
    slug.startswith("jbca-") and slug.replace("-", "").isalnum(),
    slug,
)

v = discover.inspect(b"not a pdf at all")
check("corrupt input is rejected, not raised", not v["ok"] and "unreadable" in v["reason"], v["reason"])

print()
sys.exit(0 if ok else 1)
