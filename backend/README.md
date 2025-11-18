# Frostgate Backend

This directory contains a lightweight FastAPI scaffold so the team can iterate toward the Frostgate MVP without having to spend additional time on environment setup. The goals of this skeleton are:

1. Provide a predictable application entry point (`app/main.py`).
2. Expose a simple health endpoint so infrastructure can verify deployments.
3. Provide a `/missions` endpoint that serves static content so product work can begin ahead of persistence wiring.
4. Document how to install dependencies, run the dev server, and execute tests.

## Getting started

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## Running the dev server

```bash
uvicorn app.main:app --reload
```

## Running the tests

```bash
pytest
```
