# FrostGate Core — CONTRACT (MVP Invariants)

<!-- CONTRACT_LINT_ANCHORS
0) Principles
1) Configuration and Environment Precedence
2) Auth, Scopes, Rate Limiting
3) `/defend` Endpoint Contract
4) Telemetry Input Normalization
5) Decision Engine MVP Rules
6) Doctrine and ROE Persona Gate
7) Clock Drift
8) Persistence (Best Effort, Defined)
9) Tamper-Evident Logging (Current State)
10) `/feed/live` Contract
11) Dev Seed Contract (`FG_DEV_EVENTS_ENABLED`)
12) Non-Goals (Explicit)
13) Change Control

build_app(auth_enabled
FG_AUTH_ENABLED
FG_API_KEY
FG_SQLITE_PATH
FG_ENV
X-API-Key
Invalid or missing API key
POST /defend
event_id
clock_drift_ms
only_actionable=true
action_taken
severity
FG_DEV_EVENTS_ENABLED=1
POST /dev/seed
source == "dev_seed"
-->

This document defines non-negotiable invariants for FrostGate Core MVP.
If behavior changes, update this contract first, then code/tests.

---

## 0) Principles

- **Deterministic:** same input → same output (given same config + policy).
- **Observable:** every decision is explainable and measurable.
- **Auditable:** decisions can be persisted and reviewed later.
- **Safe-by-default:** disruptive actions are gated by doctrine/persona where applicable.
- **Fail-soft:** non-critical subsystems (diff/log chain)
  may degrade without breaking core responses.

---

## 1) Configuration and Environment Precedence

### 1.1 Auth enablement precedence

Auth enablement is determined as follows:

1) `build_app(auth_enabled=...)` argument wins when provided.
2) Else if `FG_AUTH_ENABLED` is set, it is parsed as a boolean and wins.
3) Else auth is enabled if and only if `FG_API_KEY` is present (non-empty).

### 1.2 API key expectation (global)

When auth is enabled, endpoints protected by
API key MUST accept request header `X-API-Key`
(case-insensitive handling).

Expected global key is:
- `FG_API_KEY` if set
- else `"supersecret"` as a safe local-dev fallback

### 1.3 Tenant auth (if present)

If `X-Tenant-Id` is present, tenant validation is
- enforced regardless of global auth setting:
- tenant must exist and be active
- tenant api_key must match `X-API-Key`

---

## 3) Database Path Contract (Anti-Drift)

This contract exists to prevent environment drift
(host `/var/lib/...` defaults leaking into dev/test),
and to keep tests deterministic.

### 2.1 SQLite path precedence

SQLite path resolution MUST follow:

1) `FG_SQLITE_PATH` if set (absolute or relative)
2) else:
   - if `FG_ENV` is `prod` (or production equivalent), default MUST be:
     - `/var/lib/frostgate/state/frostgate.db` (container-oriented)
   - if `FG_ENV` is `dev` or `test` (or unset), default MUST be:
     - `<repo>/state/frostgate.db`

### 2.2 Anti-drift guard (must fail in tests)

- In **non-prod**, resolving to `/var/lib/...` MUST be treated as drift.
- In **FG_ENV=test**, drift MUST raise `RuntimeError` (fail fast).
- In **dev**, drift MAY log a warning but MUST remain safe-by-default.

### 2.3 Contract enforcement

The repo MUST contain a contract test proving the non-prod default resolves repo-locally
(e.g. `tests/test_db_path_contract.py`) and it MUST
run under the standard test entrypoint.

---

## 2) Auth, Scopes, Rate Limiting

### 3.1 API key behavior

- Endpoints protected by API key MUST accept `x-api-key` / `X-API-Key` header.
- If auth is enabled and the key is missing/invalid:
  - MUST return **401** with `detail="Invalid or missing API key"`.

### 3.2 DB-backed scoped keys (forward compatible)

The system supports DB-backed scoped keys stored in sqlite table `api_keys`.

**Key formats supported:**
- **Global key:** exact string match to `FG_API_KEY` (bypass, no scope check).
- **Scoped key (NEW):** `<prefix>.<token>.<secret>`
  - `key_hash` stored = `sha256(secret)`
  - `token` is base64url(json payload), used for client-side introspection only
- **Legacy (compat):** raw keys may exist for older
tests/fixtures if present in DB.

### 3.3 Scope requirements

When scoped auth is used:
- `/defend` requires scope: `defend:write`
- `/feed/live` requires scope: `feed:read`
- `/decisions` (if exposed) requires scope: `decisions:read`

Missing/insufficient scope MUST return **403** (Forbidden) from the scope layer.

**Important:** If auth is disabled, scope enforcement is effectively
bypassed (by definition).

### 3.4 Rate limiting

- `/defend` is protected by `rate_limit_guard`.
- Rate limit failures MUST return a **non-2xx** response (commonly 429).
- Contract requirement: **not 2xx**.

---

## 3) `/defend` Endpoint Contract

### 4.1 Route

- `POST /defend`
- Response model: `DefendResponse`

### 4.2 Required response fields

Response MUST include:
- `explanation_brief`: string (never null)
- `threat_level`: one of `none | low | medium | high | critical`
- `mitigations`: list (may be empty)
- `explain`: object (always present)
- `clock_drift_ms`: integer
- `event_id`: string (sha256 hex)

### 4.3 explain object requirements

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

### 5.1 Event type resolution (canonical)

Event type MUST resolve as the first non-empty:
1) `req.event_type`
2) `req.payload.event_type` if payload is dict
3) `req.event.event_type` if event is dict
4) `"unknown"`

### 5.2 Event payload resolution (canonical)

Payload MUST resolve as the first non-empty dict:
1) `req.event`
2) `req.payload`
3) `{}`

### 5.3 Source IP extraction

Source IP MUST resolve as first non-empty:
`src_ip | source_ip | source_ip_addr | ip | remote_ip`

### 5.4 Failed auth counter extraction

Failed auth count MUST resolve as first present:
`failed_auths | fail_count | failures | attempts | failed_attempts | 0`

Then MUST be coerced to integer, fallback 0.

---

## 6) Decision Engine (MVP Rules)

### 6.1 Rule scoring

Rules contribute integer points as configured in `RULE_SCORES`.
Total score = sum of triggered rule scores.

### 6.2 Threat mapping

- score >= 80 → high
- score >= 50 → medium
- score >= 20 → low
- else → none

### 6.3 Brute-force rule (MVP)

If:
- `event_type ∈ {auth, auth.bruteforce, auth_attempt}`
- `failed_auths >= 5`
- `src_ip` exists

Then:
- MUST trigger `rule:ssh_bruteforce`
- MUST include mitigation: `block_ip` targeting `src_ip`
- MUST increase `anomaly_score` above baseline

Else:
- MUST trigger `rule:default_allow`

---

## 7) Doctrine (ROE / Persona Gate)

### 7.1 TieD always present

`explain.tie_d` MUST always exist (default/empty allowed, null forbidden).

### 7.2 Guardian + SECRET behavior

If:
- persona == guardian (case-insensitive)
- classification == SECRET (case-insensitive)

Then:
- `roe_applied` MUST be true
- `ao_required` MUST be true
- `mitigations` MUST include at most one `block_ip`
- `gating_decision` MUST be one of: `allow | require_approval | reject`

### 7.3 Gating decision logic (MVP)

Default: allow
For guardian+SECRET:
- If any disruptive mitigation exists (`block_ip`), gating_decision MUST be `require_approval`
- Else allow

### 7.4 Impact heuristics (MVP)

TieD MUST include:
- `service_impact` float in [0.0, 1.0]
- `user_impact` float in [0.0, 1.0]

Baseline heuristic:
- If any `block_ip` exists:
  - `service_impact >= 0.35` (before doctrine reduction)
  - `user_impact >= 0.20` (before doctrine reduction)
- If disruption is limited by doctrine:
  - `service_impact` MUST NOT increase
  - `user_impact` MUST NOT increase

---

## 7) Clock Drift

### 8.1 Drift metric

`clock_drift_ms` computed from request timestamp vs server now.

Config: `FG_CLOCK_STALE_MS` (default 300000ms)

If absolute age exceeds stale threshold: `clock_drift_ms = 0`
Else: `clock_drift_ms = abs(age_ms)`

---

## 8) Persistence (Best Effort, Defined)

### 9.1 DecisionRecord insert

For each `/defend` request, the system SHOULD persist a DecisionRecord containing:
- tenant_id, source, event_id, event_type
- threat_level, anomaly_score, latency_ms
- explain_summary
- request payload + response payload

Critical invariant:
- Duplicate inserts (event_id uniqueness collisions) MUST NOT crash the endpoint.

### 9.2 Decision diff (MVP)

When possible, the system SHOULD:
- Load previous decision for same (tenant_id, source, event_type)
- Compute decision_diff between prior snapshot and current snapshot
- Persist it in `decision_diff_json` if the column exists

Failures in diff computation MUST NOT fail the request.

### 9.3 Scoped key persistence (schema-aware)

`mint_key()` MUST:
- ensure sqlite schema exists (idempotent)
- insert into `api_keys` using the schema that exists (columns may evolve)
- handle required fields such as `name` when present (NOT NULL)

Failures in key minting SHOULD fail loudly (test/dev visibility),
not silently mint unusable keys.

---

## 9) Tamper-Evident Logging (Current State)

### 10.1 Definition (MVP)

If DecisionRecord supports `prev_hash` and `chain_hash`, the system SHOULD:
- Set `prev_hash` to the previous record’s `chain_hash`
- Compute `chain_hash = sha256(prev_hash + canonical_payload)`

### 10.2 Non-guarantees

This does NOT guarantee tamper resistance against:
- DB admins rewriting history
- deletion of records
- offline edits without external anchoring

---

## 10) `/feed/live` Contract

### 11.1 Schema invariants (UI contract)

Each item returned MUST include the following presentation fields (non-null after backfill):

- `timestamp` (ISO8601 string; sourced from record `created_at`)
- `severity` (one of: info, low, medium, high, critical; derived from `threat_level` if missing)
- `title` (derived if missing)
- `summary` (derived if missing)
- `action_taken` (one of: log_only, blocked, rate_limited, quarantined; derived if missing)
- `confidence` (float; derived if missing)
- `score` (float; derived if missing)

### 11.2 Filter behavior

- `severity` query parameter is an alias for `threat_level` (DB stores threat_level)
- `only_actionable=true` must drop noise: items where `action_taken=log_only`
- AND `severity in (low, info)`
- `only_changed=true` returns only items with `changed_fields` populated
- `q=` search is DB-side and must not crash; it may be limited to indexed fields

---

## 12) Dev Seed Contract (FG_DEV_EVENTS_ENABLED)

When `FG_DEV_EVENTS_ENABLED=1`, dev-only endpoint
`/dev/seed` MUST exist and MUST be deterministic.

### 12.1 Mounting rule (hard gate)

Dev routes are mounted only when:
- `FG_DEV_EVENTS_ENABLED=1`

If not enabled, dev endpoints must behave as not present (404/405 depending on route/method).

### 12.2 Auth rule

Dev endpoints require a valid `X-API-Key` when auth is
enabled (and always run behind `verify_api_key`).

### 12.3 Endpoint

- `POST /dev/seed`
- Requires valid `x-api-key`
- MUST be unavailable when `FG_DEV_EVENTS_ENABLED != "1"` (404 preferred)

### 12.4 Seeded dataset invariants

Calling `POST /dev/seed` MUST create, at minimum:

- All seeded records have: `source == "dev_seed"`
- At least one noise record:
  - `severity in {"info","low"}` AND `action_taken == "log_only"`
- At least one actionable record:
  - `severity in {"high","critical"}` AND `action_taken in {"blocked","rate_limited","quarantined"}`
- Seeded records MUST include `created_at` so `/feed/live` can expose `timestamp`
- Actionable seeded records MUST include `decision_diff_json`

### 12.5 Behavioral proof (filtering must actually filter)

Given a dataset seeded via `/dev/seed`:
`GET /feed/live?only_actionable=true` MUST NOT return any `source=="dev_seed"`
 items where:

- `severity in {"info","low"}` AND `action_taken=="log_only"`

This contract exists to prevent silent test passes caused
 by non-representative datasets.

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
- persistence fields (including DB schema evolution assumptions)
- diff semantics
- auth key formats / scope enforcement
- DB path resolution behavior

MUST update this CONTRACT first and include tests.

---

## 15) Mission Envelope

### 15.1 Feature flag gate

Mission envelope endpoints MUST be mounted only when:
`FG_MISSION_ENVELOPE_ENABLED=1`.

### 15.2 Route surface

When enabled, the API MUST expose:

- `GET /missions`
- `GET /missions/{mission_id}`
- `GET /missions/{mission_id}/status`

---

## 16) Ring Routing

### 16.1 Feature flag gate

Ring routing endpoints MUST be mounted only when:
`FG_RING_ROUTER_ENABLED=1`.

### 16.2 Route surface

When enabled, the API MUST expose:

- `GET /rings/policies`
- `POST /rings/route`
- `GET /rings/isolation`

---

## 17) ROE Engine

### 17.1 Feature flag gate

ROE endpoints MUST be mounted only when:
`FG_ROE_ENGINE_ENABLED=1`.

### 17.2 Route surface

When enabled, the API MUST expose:

- `GET /roe/policy`
- `POST /roe/evaluate`

---

## 18) Impact Estimate

### 18.1 Schema availability

The system MUST expose an `ImpactEstimate` schema in the API layer
for reuse by future decision and ROE components.

---

## 19) Forensic Snapshot + Replay

### 19.1 Feature flag gate

Forensics endpoints MUST be mounted only when:
`FG_FORENSICS_ENABLED=1`.

### 19.2 Route surface

When enabled, the API MUST expose:

- `GET /forensics/snapshot/{event_id}`
- `GET /forensics/audit_trail/{event_id}`

---

## 20) Governance Approvals

### 20.1 Feature flag gate

Governance endpoints MUST be mounted only when:
`FG_GOVERNANCE_ENABLED=1`.

### 20.2 Route surface

When enabled, the API MUST expose:

- `GET /governance/changes`
- `POST /governance/changes`
- `POST /governance/changes/{change_id}/approve`
