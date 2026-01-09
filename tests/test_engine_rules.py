from datetime import datetime, timezone

from api.schemas import TelemetryInput
from engine import evaluate_rules


def _telemetry_auth(failed_auths: int) -> TelemetryInput:
    return TelemetryInput(
        source="edge-gateway-1",
        tenant_id="tenant-test",
        timestamp=datetime.now(timezone.utc).isoformat(),
        payload={
            # NOTE: this test uses generic "auth" but high failed_auths should still trigger bruteforce
            "event_type": "auth",
            "src_ip": "192.0.2.10",
            "failed_auths": failed_auths,
        },
    )


def test_bruteforce_rule_triggers_high_threat():
    telemetry = _telemetry_auth(failed_auths=12)

    threat_level, mitigations, rules, anomaly_score, ai_adv_score = evaluate_rules(
        telemetry
    )

    assert threat_level == "high"
    assert "rule:ssh_bruteforce" in rules
    assert any(m.action == "block" for m in mitigations)
    assert anomaly_score >= 0.0
    assert ai_adv_score >= 0.0


def test_low_auth_noise_is_not_high():
    telemetry = _telemetry_auth(failed_auths=1)

    threat_level, mitigations, rules, anomaly_score, ai_adv_score = evaluate_rules(
        telemetry
    )

    assert threat_level in {"low", "medium"}
    assert "rule:ssh_bruteforce" not in rules
