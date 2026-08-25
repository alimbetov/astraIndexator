# P1-2 CI Quality Gates — Qualification

Status: IN PROGRESS

This qualification proves that the mandatory CI gates introduced by P1-2 are executable against the current AstraIndexator codebase.

Required gates:

- `ruff check src tests`
- `ruff format --check src tests`
- `mypy src/astra_indexator`
- `python -m build`
- `pytest`

Completion rule:

```text
P1-2 QUALIFIED = all mandatory gates GREEN on the same pull-request revision
```

OCR model download and real ONNX inference are intentionally excluded from ordinary CI and remain separate runtime/integration evidence.
