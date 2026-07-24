---
description: Stage 2c. Turn a reference solution into an equally weighted checklist rubric.
placeholders: question, solution
---

You are writing a grading rubric for one question from a business case exam.

Convert the instructor's reference solution into a checklist of independent, equally
weighted criteria. Each criterion is worth exactly one point and must be gradable as
satisfied or not satisfied by reading a candidate answer.

Rules:
1. Every criterion must be supported by the reference solution. Do not add expectations the
   instructor did not state.
2. One idea per criterion. Split compound expectations apart.
3. State required numbers, names, and recommendations explicitly inside the criterion, so a
   grader never has to consult the solution to apply it.
4. Write each criterion as a sentence beginning "Answer ...", e.g.
   "Answer correctly computes break-even units as 12,000."
5. Produce between 3 and 12 criteria, in the order the reference solution addresses them.
6. For subjective questions, credit the reasoning moves and trade-offs the instructor
   expects rather than a single verdict, unless the instructor commits to a verdict.

Return a JSON array of strings and nothing else.

Question:
```
{question}
```

Reference solution:
```
{solution}
```
