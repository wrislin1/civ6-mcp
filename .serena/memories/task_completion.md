# Task completion

- Run tests proportional to changed surface; arena changes require `uv run --extra test pytest tests/arena -q`.
- Run focused tests first for fast diagnosis, then the required full suite.
- Run `git diff --check` immediately before each commit.
- Inspect `git status --short` and the committed diff; do not touch unrelated/untracked arena run artifacts.
- Live Civ/FireTuner validation is separate attended work unless the task explicitly requires it.