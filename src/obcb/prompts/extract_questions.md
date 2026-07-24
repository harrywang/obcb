---
description: Stage 2b. Recover question/reference-solution pairs from the instructor teaching note. This prompt decides which questions exist in the benchmark at all.
placeholders: case_text, instructor_text
---

You are building an exam-style benchmark from a business school case and its instructor
teaching note.

The teaching note contains discussion questions together with the instructor's own answers.
Your job is to recover those question/answer pairs faithfully.

Hard rules:
1. Extract ONLY questions for which the teaching note supplies an explicit reference answer.
   Skip any question the note poses but never answers.
2. The "solution" must be drawn from the teaching note. Do not invent analysis, do not
   compute new numbers, and do not answer from the case narrative yourself. Preserve the
   instructor's figures, calculations, and recommendations exactly as given.
3. Rewrite each question so it is self-contained: a competent reader who has the full case
   narrative but not the teaching note must be able to answer it. Inline any data the
   question depends on that the note supplied inside the question stem itself.
4. Keep the instructor's scope. Do not merge several questions into one or split one into
   several.

Return a JSON array. Each element is an object with exactly these keys:
- "question": the self-contained exam-style prompt, in markdown.
- "solution": the reference answer taken from the teaching note, in markdown.
- "task_description": a short imperative phrase naming the task, e.g. "Compute break-even
  volume and revenue." or "Recommend a go-to-market scenario."

Return only the JSON array. Return [] if the note contains no answered questions.

Case narrative:
```
{case_text}
```

Instructor teaching note:
```
{instructor_text}
```
