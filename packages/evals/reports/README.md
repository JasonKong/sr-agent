# Eval reports

One JSON and one Markdown file per `uv run sr-evals run` against the model API, named
`<UTC time>-<model>[-label]`. Each compares itself with the previous report for the same
dataset version and case set. Full runs, with transcripts, stay in `var/evals/runs/`
(git-ignored); dry runs with the oracle go to `var/evals/reports/`.
