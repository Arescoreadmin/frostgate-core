"""Mission-related helpers for the Frostgate API."""

from ..schemas import Mission


def list_missions() -> list[Mission]:
    """Return static mission data until persistence is wired up."""

    return [
        Mission(
            id="ops-001",
            name="Initialize Frostgate",
            status="planning",
            summary="Lay down infrastructure, observability, and CI/CD foundations.",
        ),
        Mission(
            id="ops-002",
            name="Deploy scout beacons",
            status="blocked",
            summary="Integrate telemetry stream so we can observe energy fluctuations along the perimeter.",
        ),
        Mission(
            id="ops-003",
            name="Secure the breach",
            status="ready",
            summary="Finalize MVP workflows to coordinate responders once Frostgate goes live.",
        ),
    ]
