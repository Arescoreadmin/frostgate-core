# Frostgate Core

This repository now ships with a minimal FastAPI backend so the team can start iterating toward the MVP immediately. The scaffold includes:

- A ready-to-run FastAPI application with `/`, `/health`, and `/missions` endpoints.
- A `pyproject.toml` with runtime and development dependencies pinned for Python 3.11.
- Basic pytest smoke tests that document the expected responses.
- Documentation (`backend/README.md`) covering setup, running the dev server, and executing tests.

## Repo layout

```
backend/
  app/
    main.py
    api/routes.py
  tests/
README.md
```

Use `uvicorn app.main:app --reload` from inside the `backend` directory to run the service locally after installing dependencies with `pip install -e .[dev]`.
