from pathlib import Path


def test_main_py_not_truncated():
    txt = Path("api/main.py").read_text(encoding="utf-8")
    assert "def build_app" in txt
    assert "PYp =" not in txt
    # Default app instance must exist, but auth mode can be env-derived
    assert "app = build_app" in txt
