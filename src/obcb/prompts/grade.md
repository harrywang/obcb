---
description: Stage 3. LLM-as-judge: score an attempted answer against the checklist rubric.
placeholders: case_summary, grading_rubric_list, model_answer, question, solution
source: reference-paper-code/pipeline/prompts/evaluate_models_grading.py
verbatim: true
---

You are an experienced judge of LLM-generated answers. You are given a LLM-generated answer to a question about a business case and a grading rubric to use to grade the model's answer to the question.

Remember that business cases sometimes involve subjective questions meant to test critical-thinking and reasoning skills that have no objective correct answers. Other times, there are objective questions that have a single correct answer.

You will be provided a rubric of criteria items to grade the LLM-generated answer against along with an example of a gold standard answer or description of an answer to the question taken from the instructors notes of this business case.

**Your task:** Reason through each criteria item and grade the LLM-generated answer against the criteria item. Each criteria item is worth 1 point and you must assign 0 or 1 points for each criteria item.

**Output:** Write out your reasoning for each criteria and then please return your final total score for the LLM-generated answer in between << and >> brackets. 

Question:
```
{question}
```

The LLM-Generated Answer:
```
{model_answer}
```

The Grading Rubric:
```
{grading_rubric_list}
```

Gold Standard Answer:
```
{solution}
```

Case Summary:
```
{case_summary}
```
