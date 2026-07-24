---
description: Stage 3. Ask a solver model to answer one question with the full case in context. Sent with a leading and trailing newline so it matches the reference byte-for-byte.
placeholders: case_clean_text, question
source: reference-paper-code/pipeline/prompts/evaluate_models_grading.py
verbatim: true
---

You are given a business case and a question about the case. You must output an answer thoroughly addressing the question and instructions along with full reasoning and justification for the answer.

The Question:
```
{question}
```

The Case:
```
{case_clean_text}
```
