---
name: fastroute-mcp
description: Use when inspecting, routing, monitoring, diagnosing, or comparing PCB autorouting jobs through the FastRoute MCP server. Covers safe asynchronous routing workflows, checkpoint resume, report interpretation, and avoiding constraint relaxation.
---

# FastRoute MCP

Use this skill whenever the user asks to route a PCB, inspect routing readiness, monitor a FastRoute job, diagnose unrouted nets, or compare routing attempts through the `fastroute-mcp` tools. It assumes the MCP server is available and that the FastRoute binary has been discovered.

## Core principles

1. **Routing is asynchronous.** `start_routing` returns a `job_id` immediately. Never wait inside one tool call for routing to finish; poll with `get_routing_status` / `get_routing_events`.
2. **Never mutate the source board.** All work happens in isolated job workspaces. The original `.kicad_pcb` or `.dsn` must remain byte-identical.
3. **Never relax electrical constraints.** Clearance, width, differential gap, skew, impedance, keepouts, and layer restrictions are design rules, not optimization knobs. Do not change them through routine tools.
4. **Resume is a new attempt.** There is no pause. `resume_routing` creates a new job using `--initial-session` from the prior best SES.
5. **FastRoute's own report is not KiCad DRC.** Always validate routed output separately with `validate_routing_result` when KiCad is available.

## Canonical workflow

### 1. Inspect before routing
- `inspect_board(board_path=...)` to get layers, components, nets, tracks, vias, unconnected count.
- `get_board_rules(board_path=...)` for design rules and net classes.
- `get_differential_pairs(board_path=...)` if impedance/length matching matters.
- Do not start routing until the board has an outline and basic rules are known.

### 2. Validate configuration
- `get_routing_profiles()` to list `quick`, `balanced`, `quality`, `dense_board`, `parity`.
- `validate_routing_config(profile=..., overrides=...)` before starting. Reject unknown `--router.*` keys are rejected by the server; do not attempt raw CLI fragments.
- `apply_routing_profile(profile=..., overrides=...)` returns a validated config object to pass later.
- Show existing-track handling to the user before starting if tracks may move.

### 3. Start routing
- `start_routing(board_path=..., profile=..., max_time_seconds=..., diagnose=..., live_monitoring=...)`.
- Required safe choices:
  - Use a distinct output workspace (handled by server).
  - Prefer `existing_tracks="router_default"` only with an explicit warning; otherwise use `keep_existing` or `clear_unlocked`.
  - Set a bounded `max_time_seconds`.
  - Only set `diagnose=true` if the user accepts the extra compute cost.
- Return the `job_id` and stop. Do not poll in the same request unless the user asked for monitoring.

### 4. Monitor
- `get_routing_status(job_id=...)` for state, elapsed time, stage, latest pass, unrouted/violations, best SES, report availability, event stream health, and next recommended action.
- `get_routing_events(job_id=..., after_event_id=..., limit=...)` for normalized pass/stage/progress/log events.
- `get_routing_logs(job_id=..., stream=..., after_line=..., limit=...)` for raw stdout/stderr.
- Never fabricate a percentage. Use `null` when the engine did not provide a denominator.

### 5. Stop safely
- `stop_routing(job_id=..., force=false, grace_seconds=...)` sends exactly one graceful signal.
- Do not set `force=true` without explicit user approval.
- After stop, check `checkpoint_exists` before treating the SES as usable.

### 6. Read results
- `get_routing_report(job_id=...)` for normalized metrics.
- `get_unrouted_connections(job_id=..., diagnosis_class=..., net_names=..., limit=..., cursor=...)`.
- `get_clearance_violations(job_id=..., include_unfixable=...)`.
- `diagnose_unrouted_nets(job_id=..., run_diagnosis_if_missing=...)` only if diagnosis is missing and the user approves a diagnostic rerun.

### 7. Validate and import
- `validate_routing_result(job_id=..., run_kicad_drc=..., refill_zones=...)` after a routed board exists.
- `export_routing_artifacts(job_id=..., artifacts=[...])` for `ses`, `report`, `routed_board`, `drc_report`, `logs`, `manifest`.
- `import_routing_result(job_id=..., output_board_path=..., approval_token=...)` requires an approval token and must never overwrite the source board.

### 8. Compare and iterate
- `compare_routing_runs(job_ids=..., metrics=...)` aligns metrics and flags non-comparable runs when board hashes or config hashes differ.
- `resume_routing(source_job_id=..., profile=..., max_time_seconds=..., multi_start=..., diagnose=..., approved_board_hash=...)` starts a new attempt from the best SES.
- Ask for approval before additional attempts unless the user authorized a bounded multi-run policy.

## State machine

Valid states: `created`, `queued`, `preparing`, `running`, `stop_requested`, `finalizing`, `completed`, `completed_partial`, `stopped`, `failed`, `cancelled_before_start`, `interrupted`. There is no `paused` state. `interrupted` is used after server restart for jobs whose process disappeared.

## Report interpretation

FastRoute report fields (normalized by the server):
- `unrouted_count`: unrouted connections.
- `ignored_unrouted_count`: ignored-class unrouted connections; report separately, not as ordinary failures.
- `violations_count` and `unfixable_violations_count`.
- `vias_count`, `traces_count`, `trace_length_mm`.
- `router_score`, `optimizer_score`.
- `unrouted_connections[]`: each has `net`, endpoints, coordinates, and diagnosis.
- `clearance_violations[]`: layer, location, required/actual clearance, unfixable flag, involved items.

Diagnosis classes:
- `congestion`: at least one endpoint can route alone; try multi-start, longer bounded run, or placement changes.
- `blocked`: neither endpoint can route alone; likely geometry/rules issue, recommend PCB design review.
- Ignored nets are not routing failures.

Multi-pin caveat: for nets with more than two pins, FastRoute's per-connection diagnosis connects an endpoint to the nearest part of its net, not necessarily the displayed airline endpoint. Do not overstate diagnosis as proof of root cause.

## Decision rules

- If `unrouted == 0` and KiCad DRC has no new violations, mark the run candidate complete.
- If unrouted decreases and violations do not increase, it is a candidate improvement.
- If unrouted stagnates across two attempts, inspect diagnostics before another run.
- If all remaining are `blocked`, recommend PCB design review instead of endless retries.
- If remaining are mostly `congestion`, a new multi-start order or longer bounded run may help.
- If KiCad DRC detects new unconnected items or violations, do not finalize even if FastRoute looks good.

## Common pitfalls

- FastRoute flags require `=` form: `--max-time=`, `--report=`, `--live=`, `--multi-start=`, `--initial-session=`, `--router.*=`. The server handles this, but manual reasoning should not assume space-separated flags.
- `--report` is not generated unless the correct flag form is used.
- The live SSE stream is best-effort with bounded queues; do not use it as the durable database. Final status comes from process exit plus report.
- SSE disconnect does not kill the job.
- `POST /stop` on the live viewer is unauthenticated loopback only; do not expose it.
- `keep_existing`/`clear_unlocked` are KiCad-side semantics; `router_default` may move unlocked tracks.
- Do not start duplicate jobs for the same board/config unless explicitly requested.
- Do not expose arbitrary executable paths or raw CLI fragments.
- Do not call `import_routing_result` without an approval token.
- Do not declare a winner using a single score when hard DRC or constraint regressions exist.

## Troubleshooting

- `FASTROUTE_NOT_FOUND`: set `FASTROUTE_BIN` or build `target/debug/fastroute` (or release).
- `KICAD_NOT_FOUND`: set `KICAD_PYTHON`/`KICAD_CLI`; board inspection/import/DRC unavailable otherwise, but DSN-based routing still works.
- `EXPORT_FAILED`: board lacks outline on Edge.Cuts or KiCad cannot load it.
- `REPORT_MISSING`: process exited before writing report; inspect logs and exit code.
- `CHECKPOINT_MISSING`: no best SES; job likely failed early.
- `GRACEFUL_STOP_TIMEOUT`: process did not exit within grace period; only force-kill with explicit approval.
- `CONFIG_REJECTED`: unknown or unsafe option; use allowlisted keys only.

## Minimal example

1. `inspect_board(board_path="board.kicad_pcb")`
2. `validate_routing_config(profile="balanced", overrides={"max_time_seconds": 300})`
3. `start_routing(board_path="board.kicad_pcb", profile="balanced", max_time_seconds=300, diagnose=false, live_monitoring=true)`
4. Poll `get_routing_status(job_id=...)` until terminal.
5. `get_routing_report(job_id=...)`
6. `validate_routing_result(job_id=..., run_kicad_drc=true)` if KiCad is available.
PY