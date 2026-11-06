# Contributing to Moltskin

Thanks for helping. Moltskin is a local-first multi-profile browser manager built on
[Camoufox](https://github.com/daijro/camoufox).

## Development setup

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/camoufox fetch
.venv/bin/uvicorn moltskin.services.core.main:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765>. API docs: <http://127.0.0.1:8765/docs>.

## Tests

```bash
.venv/bin/python moltskin/tests/test_m0.py
.venv/bin/python moltskin/tests/test_m0.py --headful
```

## Pull requests

- Keep changes scoped. One concern per PR.
- Do not modify the `camoufox/` upstream clone in this repository.
- Add or update tests when behaviour changes.
- Update `README.md` / `README.zh-CN.md` when user-facing behaviour changes.
- Run `ruff check --select E9,F services` before submitting.

## Code style

- Python 3.10+ typing, `from __future__ import annotations`.
- No new dependencies without a reason.
- UI: plain HTML/CSS/JS, no build step, no framework.
- Never log proxy passwords, cookies, or API tokens.
