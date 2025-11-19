import importlib

import pytest


@pytest.mark.parametrize(
    "mod_path",
    [
        "jobs.sim_validator.job",
        "jobs.merkle_anchor.job",
        "jobs.chaos.job",
    ],
)
def test_job_modules_import_if_present(mod_path: str):
    """
    Smoke-test job modules:
      - If present, they must import cleanly.
      - If not present yet, test is skipped (MVP-friendly).
    """
    try:
        importlib.import_module(mod_path)
    except ModuleNotFoundError:
        pytest.skip(f"{mod_path} not present in this repo yet")
