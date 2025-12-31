# FrostGate Core — CONTRACT (MVP Invariants)

This document defines **non-negotiable invariants** for FrostGate Core MVP.  
If behavior changes, update this contract **first**, then code/tests.

---

## 0) Core Principles

- **Deterministic:** same input → same output (within configured policy).
- **Observable:** every decision is explainable and measurable.
- **Auditable:** decisions can be persisted and reviewed later.
- **Safe-by-default:** disruptive actions are gated by doctrine/persona.

---

## 1) API Auth + Scopes + Rate Limit

### 1.1 API Key
- `/defend` requires `x-api-key` unless auth is explicitly disabled in dev/test harness.
- Invalid/missing key must return **401/403** (as configured by auth layer).

### 1.2 Scopes
- `/defend` requires scope: `defend:write`.

### 1.3 Rate limiting
- `/defend` is protected by `rate_limit_guard`.
- Rate limit failures must return a non-2xx response (exact code depends on rate limit module).

---

## 2) `/defend` Endpoint Contract

### 2.1 Route
- POST `/defend`
- Response model: `DefendResponse`

### 2.2 Required Response Fields
Response MUST include:
- `explanation_brief`: **string** (never null)
- `threat_level`: one of `none | low | medium | high`
- `mitigations`: list (may be empty)
- `explain`: object (always present)
- `clock_drift_ms`: integer
- `event_id`: string (sha256 hex)

### 2.3 `explain` Object Requirements
`explain` MUST include:
- `summary`: string
- `rules_triggered`: list[string]
- `anomaly_score`: float
- `score`: int
- `tie_d`: **TieD** object (never null)

Also surfaced:
- `roe_applied`: bool
- `disruption_limited`: bool
- `ao_required`: bool
- `persona`: string|null
- `classification`: string|null

---

## 3) Telemetry Input Normalization Rules

### 3.1 Event Type (canonical)
Event type MUST resolve as:
1) `req.event_type` if present  
2) else `req.payload.event_type` if payload is dict  
3) else `req.event.event_type` if event is dict  
4) else `"unknown"`

### 3.2 Event Payload (canonical)
Payload MUST resolve as:
1) `req.event` if dict and non-empty  
2) else `req.payload` if dict and non-empty  
3) else `{}`

### 3.3 IP extraction
Source IP MUST resolve as first non-empty:
`src_ip | source_ip | source_ip_addr | ip | remote_ip`

### 3.4 Failed auth counter extraction
Failed auth count MUST resolve as first present:
`failed_auths | fail_count | failures | attempts | failed_attempts | 0`
Then coerced to integer, fallback 0.

---

## 4) Decision Engine (MVP Rules)

### 4.1 Rule scoring
- Rules contribute integer points as configured in `RULE_SCORES`.
- Total score = sum of triggered rule scores.

### 4.2 Threat mapping
- score >= 80 → `high`
- score >= 50 → `medium`
- score >= 20 → `low`
- else → `none`

### 4.3 Brute-force rule (MVP)
If:
- event_type ∈ `{auth, auth.bruteforce, auth_attempt}`
- failed_auths >= 5
- src_ip exists

Then:
- MUST trigger rule `rule:ssh_bruteforce`
- MUST include mitigation: `block_ip` targeting `src_ip`
- MUST increase anomaly_score above baseline

Else:
- MUST trigger `rule:default_allow`

---

## 5) Doctrine (ROE / Persona Gate)

### 5.1 TieD always present
`explain.tie_d` MUST always exist (empty/default is allowed, null is not).

### 5.2 Guardian + SECRET behavior
If:
- persona == `guardian` (case-insensitive)
- classification == `SECRET` (case-insensitive)

Then:
- `roe_applied` MUST be `true`
- `ao_required` MUST be `true`
- `mitigations` MUST include **at most one** `block_ip`
- `gating_decision` MUST be one of:
  - `allow`
  - `require_approval`
  - `reject`

### 5.3 Gating decision logic (MVP)
- Default: `allow`
- For guardian+SECRET:
  - If any disruptive mitigation exists (`block_ip`), gating_decision MUST be `require_approval`
  - Else `allow`

### 5.4 Impact heuristics (MVP)
TieD MUST include:
- `service_impact` float in [0.0, 1.0]
- `user_impact` float in [0.0, 1.0]

MVP heuristic baseline:
- If any `block_ip` exists:
  - service_impact >= 0.35 (before doctrine reduction)
  - user_impact >= 0.20 (before doctrine reduction)

If disruption is limited by doctrine:
- service_impact MUST NOT increase
- user_impact MUST NOT increase

---

## 6) Clock Drift

### 6.1 Drift metric
- `clock_drift_ms` computed from request timestamp vs server now.
- Config: `FG_CLOCK_STALE_MS` (default 300000ms)
- If absolute age exceeds stale threshold, `clock_drift_ms = 0`
- Else `clock_drift_ms = abs(age_ms)`

---

## 7) Persistence (Best Effort, But Defined)

### 7.1 DecisionRecord insert
For each `/defend` request, system SHOULD persist a `DecisionRecord` containing:
- tenant_id, source, event_id, event_type
- threat_level, anomaly_score, latency_ms
- explain_summary
- request payload + response payload (as JSON or serialized string depending on DB column types)

Duplicates (event_id uniqueness) MUST NOT crash the endpoint.

### 7.2 Decision Diff (MVP)
When possible, system SHOULD:
- Load previous decision for same `(tenant_id, source, event_type)`
- Compute decision_diff between prior snapshot and current snapshot
- Persist it in `decision_diff_json` if the column exists

Failures in diff computation MUST NOT fail the request.

---

## 8) Tamper-Evident Logging (Current State)

### 8.1 What “tamper-evident” means here
If `DecisionRecord` supports `prev_hash` and `chain_hash`, the system SHOULD:
- Set `prev_hash` to the previous record’s `chain_hash`
- Compute `chain_hash = sha256(prev_hash + canonical_payload)`

This provides **best-effort tamper evidence** within a single DB history.

### 8.2 What it does NOT guarantee (yet)
This does NOT guarantee tamper resistance against:
- DB admins rewriting history
- deletion of records
- offline edits without external anchoring

To become stronger, future phases may add:
- periodic anchoring to an append-only store or external timestamping
- signed records (tenant/private key)
- write-once storage mode

---

## 9) Non-Goals (Explicit)
MVP does NOT guarantee:
- full EDR-grade rule coverage
- real-time enforcement at network edge
- cryptographic non-repudiation across hostile admins
- multi-tenant policy governance (OPA) beyond current scaffolding

---

## 10) Change Control
Any change that impacts:
- `/defend` response schema
- doctrine behavior
- persistence fields
- diff semantics
MUST update this CONTRACT first and include tests.