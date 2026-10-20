# volab

A volatility-forecasting research lab. A deterministic pipeline owns the data, the
splits and the scoring, so that development code cannot read from the sealed holdout
window and flatter its own results.

## Getting started

```bash
uv sync
uv run pytest
```

## The holdout rule

All reads go through `load_panel()`. Passing an `end` beyond `dev_end` requires both
keys to be present, and fails with `HoldoutViolation` otherwise.
