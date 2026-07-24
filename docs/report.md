# The HTML report

[← README](../README.md)

`obcb report` writes `report.html` alongside the markdown and JSON — one self-contained file,
no CDN, no build step, openable from disk and shareable as-is.

```bash
uv run obcb report          # writes report.md, report.html, scores.json
uv run obcb html            # rebuild just the HTML from scores.json
uv run obcb html --out ~/Desktop/bench.html
```

It shows a masthead, headline stat tiles, Standard and Complete Answer scoring with bootstrap
confidence intervals, breakdowns by question type and discipline, spend, a **case & question
detail** view (each extracted question with its reference solution, rubric, and every model's
answer and score — the builder's output made inspectable), and the configuration that produced
the numbers — in an editorial research-report style (serif headlines, a teal
accent, mono tabular numbers, hairline-bordered cards, and a callout for the contamination
caveat).

A few deliberate choices, so later edits do not undo them:

- **Colour encodes the model, everywhere.** One model keeps one hue down the whole page, so
  the eye tracks an entity rather than a rank. The two metrics are separated by faceting into
  two charts, not by colouring a second bar.
- **Every bar is labelled and a table ships.** The palette was checked with a contrast
  validator; on the light surface the third hue falls below 3:1, which obliges relief. Values
  sit at each bar tip and the full numbers are in a table, so nothing is gated behind colour
  perception.
- **Light by default.** The page renders light regardless of the viewer's OS setting — a
  dark-mode machine still gets a light report. A dark variant exists behind an explicit
  `data-theme="dark"` opt-in, using the same hues re-stepped for a dark surface.
- **Model names are escaped.** They come from configuration, so they are treated as untrusted
  text rather than markup — asserted in `tests/html_check.py`.
