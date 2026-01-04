# FrostGate Core — CONTRACT (MVP Invariants)

This document defines non-negotiable invariants for FrostGate Core MVP.  
If behavior changes, update this contract first, then code/tests.

> Rule: If tests and CONTRACT disagree, CONTRACT wins. Fix the implementation or update CONTRACT intentionally.

---

## Table of Contents

- 0) Principles
- 1) Configuration and Environment Precedence
- 2) Auth, Scopes, Rate Limiting
- 3) `/defend` Endpoint Contract
- 4) Telemetry Input Normalization
- 5) Decision Engine MVP Rules
- 6) Doctrine and ROE Persona Gate
- 7) Clock Drift
- 8) Persistence (Best Effort, Defined)
- 9) Tamper-Evident Logging (Current State)
- 10) `/feed/live` Contract
- 11) Dev Seed Contract (`FG_DEV_EVENTS_ENABLED`)
- 12) Non-Goals (Explicit)
- 13) Change Control

---

## 0) Principles

**Deterministic:** same input → same output (given same config + policy).  
**Observable:** every decision is explainable and measurable.  
**Auditable:** decisions can be persisted and reviewed later.  
**Safe-by-default:** disruptive actions are gated by doctrine/persona where applicable.  
**Fail-soft:** non-critical subsystems (diff/log chain) may degrade without breaking core responses.

---

## 1) Configuration and Environment Precedence

This section exists to prevent “it works on my machine” drift and accidental auth exposure.

### 1.1 Auth enablement precedence

Auth enablement is determined as follows:

1) `build_app(auth_enabled=...)` argument wins when provided.  
2) Else if `FG_AUTH_ENABLED` is set, it is parsed as a boolean and wins.  
3) Else auth is enabled if and only if `FG_API_KEY` is present.

This is deliberate to keep tests deterministic and prevent accidental exposure.

### 1.2 API key expectation

Global auth uses request header `X-API-Key` (case-insensitive).  
Expected key is:
- `FG_API_KEY` if set
- else `"supersecret"` as a safe local-dev fallback

### 1.3 Dev endpoint gating

Dev routes MUST be mounted only when:
- `FG_DEV_EVENTS_ENABLED=1`

If not enabled, dev endpoints must behave as not present (404 preferred; 405 acceptable if router exists but method not allowed).

---

## 2) Auth, Scopes, Rate Limiting

### 2.1 API key behavior

Endpoints protected by API key MUST accept `x-api-key` (case-insensitive header handling).

If auth is enabled and the key is missing/invalid:
- MUST return **401** with `detail="Invalid or missing API key"`.

Auth enabling rules:
- Explicit `FG_AUTH_ENABLED` MUST override other knobs.
- If `FG_AUTH_ENABLED` is not set, presence of `FG_API_KEY` MAY enable auth.

### 2.2 Scopes

`/defend` requires scope: `defend:write`.

Missing/insufficient scope MUST return **403** (Forbidden) from the scope layer.

### 2.3 Rate limiting

`/defend` is protected by `rate_limit_guard`.

Rate limit failures MUST return a non-2xx response.  
Response code is owned by the rate limiter (commonly 429). Contract requires: **not 2xx**.

---

## 3) `/defend` Endpoint Contract

### 3.1 Route

**POST /defend**

### 3.2 Required response fields

Response MUST include:

- `explanation_brief`: string (never null)
- `threat_level`: one of `none | low | medium | high | critical`
- `mitigations`: list (may be empty)
- `explain`: object (always present)
- `clock_drift_ms`: integer
- `event_id`: string (sha256 hex)

### 3.3 explain object requirements

`explain` MUST include:

- `summary`: string
- `rules_triggered`: list[string]
- `anomaly_score`: float
- `score`: int
- `tie_d`: TieD object (never null)

Also surfaced (may be null depending on config):
- `roe_applied`: bool
- `disruption_limited`: bool
- `ao_required`: bool
- `persona`: string | null
- `classification`: string | null

---

## 4) Telemetry Input Normalization

### 4.1 Event type resolution (canonical)

Event type MUST resolve as the first non-empty:

1) `req.event_type`  
2) `req.payload.event_type` if payload is dict  
3) `req.event.event_type` if event is dict  
4) `"unknown"`

### 4.2 Event payload resolution (canonical)

Payload MUST resolve as the first non-empty dict:

1) `req.event`  
2) `req.payload`  
3) `{}`

### 4.3 Source IP extraction

Source IP MUST resolve as first non-empty:
`src_ip | source_ip | source_ip_addr | ip | remote_ip`

### 4.4 Failed auth counter extraction

Failed auth count MUST resolve as first present:
`failed_auths | fail_count | failures | attempts | failed_attempts | 0`

Then MUST be coerced to integer, fallback 0.

---

## 5) Decision Engine MVP Rules

### 5.1 Rule scoring

Rules contribute integer points as configured in `RULE_SCORES`.  
Total score = sum of triggered rule scores.

### 5.2 Threat mapping

- score >= 80 → high  
- score >= 50 → medium  
- score >= 20 → low  
- else → none

### 5.3 Brute-force rule (MVP)

If:

- event_type ∈ {`auth`, `auth.bruteforce`, `auth_attempt`}
- failed_auths >= 5
- src_ip exists

Then:

- MUST trigger `rule:ssh_bruteforce`
- MUST include mitigation: `block_ip` targeting src_ip
- MUST increase anomaly_score above baseline

Else:

- MUST trigger `rule:default_allow`

---

## 6) Doctrine and ROE Persona Gate

### 6.1 TieD always present

`explain.tie_d` MUST always exist (default/empty allowed, null forbidden).

### 6.2 Guardian + SECRET behavior

If:

- persona == guardian (case-insensitive)
- classification == SECRET (case-insensitive)

Then:

- roe_applied MUST be true
- ao_required MUST be true
- mitigations MUST include at most one `block_ip`
- gating_decision MUST be one of: `allow | require_approval | reject`

### 6.3 Gating decision logic (MVP)

Default: `allow`.

For guardian+SECRET:
- If any disruptive mitigation exists (`block_ip`), gating_decision MUST be `require_approval`
- Else `allow`

### 6.4 Impact heuristics (MVP)

TieD MUST include:
- service_impact float in [0.0, 1.0]
- user_impact float in [0.0, 1.0]

Baseline heuristic:
- If any `block_ip` exists:
  - service_impact >= 0.35 (before doctrine reduction)
  - user_impact >= 0.20 (before doctrine reduction)

If disruption is limited by doctrine:
- service_impact MUST NOT increase
- user_impact MUST NOT increase

---

## 7) Clock Drift

### 7.1 Drift metric

`clock_drift_ms` computed from request timestamp vs server now.

Config: `FG_CLOCK_STALE_MS` (default 300000ms)

If absolute age exceeds stale threshold:
- clock_drift_ms = 0

Else:
- clock_drift_ms = abs(age_ms)

---

## 8) Persistence (Best Effort, Defined)

### 8.1 DecisionRecord insert

For each `POST /defend` request, the system SHOULD persist a DecisionRecord containing:

- tenant_id, source, event_id, event_type
- threat_level, anomaly_score, latency_ms
- explain_summary
- request payload + response payload (JSON or serialized string depending on schema)

Critical invariant:
- Duplicate inserts (event_id uniqueness collisions) MUST NOT crash the endpoint.

### 8.2 Decision diff (MVP)

When possible, the system SHOULD:

- load previous decision for same (tenant_id, source, event_type)
- compute decision_diff between prior snapshot and current snapshot
- persist it in decision_diff_json if the column exists

Failures in diff computation MUST NOT fail the request.

---

## 9) Tamper-Evident Logging (Current State)

### 9.1 Definition (MVP)

If DecisionRecord supports prev_hash and chain_hash, the system SHOULD:

- set prev_hash to the previous record’s chain_hash
- compute chain_hash = sha256(prev_hash + canonical_payload)

This provides best-effort tamper evidence within a single DB history.

### 9.2 Non-guarantees

This does NOT guarantee tamper resistance against:
- DB admins rewriting history
- deletion of records
- offline edits without external anchoring

Future strengthening may include:
- external anchoring (append-only store / timestamping)
- signed records (tenant/private key)
- write-once storage mode

---

## 10) `/feed/live` Contract

### 10.1 Schema invariants (UI contract)

Each item returned MUST include the following presentation fields (non-null after backfill):

- timestamp (ISO8601 string; sourced from record created_at)
- severity (one of: info, low, medium, high, critical; derived from threat_level if missing)
- title (derived if missing)
- summary (derived if missing)
- action_taken (one of: log_only, blocked, rate_limited, quarantined; derived if missing)
- confidence (float; derived if missing)
- score (float; derived if missing)

### 10.2 Filter behavior

- severity query parameter is an alias for threat_level (DB stores threat_level)
- `only_actionable=true` must drop noise: items where action_taken=log_only AND severity in (low, info)
- only_changed=true returns only items with changed_fields populated
- q= search is DB-side and must not crash; it may be limited to indexed fields

---

## 11) Dev Seed Contract (`FG_DEV_EVENTS_ENABLED`)

When `FG_DEV_EVENTS_ENABLED=1`, dev-only endpoint `/dev/seed` MUST exist and MUST be deterministic.

### 11.1 Endpoint

**POST /dev/seed**  
Requires valid x-api-key  
MUST be unavailable when `FG_DEV_EVENTS_ENABLED != "1"` (404 preferred)

### 11.2 Seeded dataset invariants

Calling POST /dev/seed MUST create, at minimum:

- All seeded records have: source == "dev_seed"
- At least one noise record:
  - severity in {"info","low"} AND action_taken == "log_only"
- At least one actionable record:
  - severity in {"high","critical"} AND action_taken in {"blocked","rate_limited","quarantined"}
- Seeded records MUST include created_at so `/feed/live` can expose timestamp
- Actionable seeded records MUST include decision_diff_json

### 11.3 Behavioral proof (filtering must actually filter)

Given a dataset seeded via `/dev/seed`:

GET `/feed/live?only_actionable=true` MUST NOT return any source=="dev_seed" items where:
- severity in {"info","low"} AND action_taken=="log_only"

This contract exists to prevent silent test passes caused by non-representative datasets.

---

## 12) Non-Goals (Explicit)

MVP does NOT guarantee:

- full EDR-grade rule coverage
- real-time enforcement at network edge
- cryptographic non-repudiation against hostile DB admins
- full multi-tenant policy governance (OPA) beyond current scaffolding

---

## 13) Change Control

Any change impacting:

- `/defend` response schema
- doctrine behavior
- persistence fields
- diff semantics
- `/feed/live` filters or presentation backfill behavior
- `/dev/seed` determinism requirements

MUST update this CONTRACT first and include tests.

---
