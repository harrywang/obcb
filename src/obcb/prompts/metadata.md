---
description: Stage 2d. Annotate discipline, question type, and the O*NET Intermediate Work Activity.
placeholders: discipline_list, iwa_list, question, solution
---

You are annotating one question from a business case benchmark.

Return a single JSON object with exactly these keys:

- "discipline": exactly one value from this list:
{discipline_list}
- "numerical": true if answering requires any quantitative computation, false otherwise.
- "primarily_numerical": true if computation is the substance of the question rather than a
  supporting step.
- "subjective": true if competent experts could reach different defensible answers, false if
  the question has a single correct answer that can be checked.
- "subjective_reasoning": one sentence justifying the subjective label.
- "intermediate_work_activity_id": the id of the single O*NET Intermediate Work Activity
  that best describes the work this question asks the respondent to perform. Choose from the
  list below and copy the id exactly.

O*NET Intermediate Work Activities:
{iwa_list}

Return only the JSON object.

Question:
```
{question}
```

Reference solution:
```
{solution}
```
