---
description: Stage 2a. Catalogue a case: title, summary, learning objectives, fictional-vs-real.
placeholders: case_text, instructor_text
---

You are cataloguing a business school teaching case for a research dataset.

Read the case narrative and the instructor teaching note below, then return a single JSON
object with exactly these keys:

- "case_title": the case title as printed, without any journal or volume prefix.
- "case_summary": 3-5 sentences describing the situation, the decision maker, and the
  decision to be made. Written for someone who has not read the case.
- "case_learning_objectives": array of 2-6 short strings, drawn from the teaching note
  where it states them explicitly and inferred only where it does not.
- "fictional_case": true if the focal organization is invented or disguised, false if it
  is a real, named organization. Judge the focal organization only, not companies that
  are merely cited as background.
- "fictional_reasoning": one sentence justifying the fictional_case label.

Return only the JSON object.

Case narrative:
```
{case_text}
```

Instructor teaching note:
```
{instructor_text}
```
