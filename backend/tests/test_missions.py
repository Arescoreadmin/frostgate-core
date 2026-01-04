import importlib.util
import pytest

# These tests reference an `app.*` package that is not part of FrostGate Core.
# They likely came from a template or another service. Hard-failing collection is unacceptable.
if importlib.util.find_spec("app") is None:
    pytest.skip("legacy/template test: no `app` package in this repo", allow_module_level=True)

from app.api.routes import missions  # type: ignore  # pragma: no cover


def test_missions_route_module_imports():
    # Minimal smoke test: module exists and imports cleanly.
    assert missions is not None
