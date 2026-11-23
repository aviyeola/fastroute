# FastRoute MCP: Agent-Native PCB Autorouting Integration

**Implementation specification for a coding agent**\
**Repository:** https://github.com/aviyeola/fastroute\
**Inspected branch:** `main`\
**Inspection date:** 2026-11-23\
**Initial implementation language:** Python\
**Router engine:** Existing Rust FastRoute CLI; preserve engine behavior
unless a narrowly scoped change is justified.

------------------------------------------------------------------------

## 1. Mission and non-goals

Build a local MCP server that allows an AI agent to inspect a KiCad PCB
routing job, launch and supervise FastRoute, retrieve structured
progress and diagnostics, compare routing attempts, and request safe
follow-up iterations.

The first implementation must be an orchestration layer around existing
FastRoute and KiCad CLI/plugin interfaces. Do not port the routing
engine, add AI logic to Rust, or modify PCB geometry automatically.

### Goals

1.  Expose board inspection, routing control, status, logs, reports and
    diagnostics as typed MCP tools.
2.  Treat routing as a persistent asynchronous job, not a long-running
    MCP request.
3.  Reuse FastRoute's existing `.dsn` → `.ses` workflow, `--report`,
    `--diagnose`, `--initial-session`, `--max-time`, signal handling,
    live SSE and KiCad headless bridge.
4.  Preserve original board files; route in isolated per-job workspaces.
5.  Make every run reproducible and comparable.
6.  Make the agent an optimization supervisor: it can recommend and
    execute bounded reruns, but must not silently relax electrical
    constraints or edit source PCB design rules.

### Non-goals for MVP

-   No direct mutation of FastRoute internal Rust data structures.
-   No pause/resume claim. The inspected CLI supports graceful stop and
    restart from a saved `.ses`, not a verified in-place pause.
-   No automatic PCB placement, schematic edits, netlist changes,
    clearance relaxation, trace-width reduction below board constraints,
    or keepout removal.
-   No remote multi-user service in the first release.
-   No promise that FastRoute's internal report is equivalent to KiCad
    DRC. KiCad DRC is a separate final validation stage.

------------------------------------------------------------------------

## 2. Repository inspection: verified facts and caveats

The supplied repository is a fork of `parisxmas/fastroute`. The GitHub
page identifies `aviyeola/fastroute` as a fork and shows the `main`
branch. The fork's root `Cargo.toml` reports a Cargo workspace with
`crates/*`, Rust edition 2021, GPL-3.0-or-later and version `0.1.14`.

### 2.1 Verified source layout

The README documents these crates:

  -----------------------------------------------------------------------
  Crate                               Responsibility
  ----------------------------------- -----------------------------------
  `fr-dsn`                            Specctra S-expression reader and
                                      typed DSN model

  `fr-geom`                           Planar geometry

  `fr-jcompat`                        Java behavior compatibility

  `fr-settings`                       Router settings, defaults,
                                      CLI/DSN/environment settings

  `fr-engine`                         Board model, search trees,
                                      autorouter,
                                      pull-tight/shove/optimizer, DRC,
                                      scoring, pipeline

  `fr-io`                             DSN loading, post-load processing,
                                      SES reader/writer

  `fastroute`                         CLI entry point, report module and
                                      live-view module
  -----------------------------------------------------------------------

Relevant inspected source files:

-   `crates/fastroute/src/main.rs`
-   `crates/fastroute/src/report.rs`
-   `crates/fastroute/src/live.rs`
-   `integrations/kicad/plugins/core.py`
-   `integrations/kicad/plugins/route_cli.py`
-   `integrations/kicad/README.md`
-   root `Cargo.toml`

### 2.2 Existing CLI capabilities confirmed in `main.rs`

The CLI accepts:

-   `-de <design.dsn>`: input DSN.
-   `-do <output.ses>`: output session. The file is rewritten atomically
    when a better board is found.
-   `--initial-session=<file>`: import a prior SES as the starting
    wiring, which can be ripped/optimized as routed wiring.
-   `--max-time=<seconds>`: request a graceful stop after a wall-clock
    duration.
-   `--report=<file>`: write JSON result report.
-   `--diagnose`: with `--report`, diagnose each unrouted connection.
-   `--live[=<port>]`: start the live browser viewer.
-   `--multi-start=N`: shuffled-order reruns if connections remain
    unrouted.
-   `--parity`, `--no-enhancements`, `--optimizer-mode`,
    `--time-limit-mode`, `--time-limit-factor`.
-   `--pairs=<file>`, `--pair-skew`, `--tune=<file>`, `--layer-heights`,
    `--no-neckdown-classes`.
-   Freerouting-style `--router.<path>=<value>` settings and `-mp`.

The CLI reads the DSN, parses it using `fr_dsn`, derives settings from
CLI/environment/DSN through `fr_settings`, builds a `RoutingBoard`
through `fr_io`, prepares any initial session, and runs
`pipeline::run_pipeline`.

The pipeline context includes a `StopToken`, checkpoint callback,
observer callback, multi-start configuration and optimizer mode. The
checkpoint callback writes SES atomically only when the new
`CheckpointKey` is better than the previous best. A first
Ctrl+C/SIGTERM/Ctrl+Break requests graceful stop; a second signal exits
immediately. This is a graceful stop, not a pause.

### 2.3 Existing live view confirmed in `live.rs`

The live view is a small HTTP server bound to `127.0.0.1`, normally port
7878, trying subsequent ports if occupied. It exposes:

-   `GET /` and `/index.html`: viewer.
-   `GET /events`: Server-Sent Events (SSE).
-   `POST /stop`: requests the router's `StopToken`.

Observed SSE event types include:

-   `board`: static board geometry and metadata.
-   `frame`: current traces, vias and airlines.
-   `tick`: progress inside a pass.
-   `pass`: router/optimizer pass metrics.
-   `stage`: stage transitions.
-   `log`: log lines.
-   `done`: completion.

The pipeline observer feeds `LiveEvent` values to this view. Frames are
throttled and only generated for connected viewers (except forced
frames). Event backlog is bounded; slow clients may miss queued updates.
Therefore, SSE is useful for live monitoring but must not be treated as
the durable job database or the only source of final truth.

`POST /stop` is an unauthenticated loopback endpoint in the current
source. Do not expose it to a network interface or blindly forward
untrusted requests to it.

### 2.4 Existing report and diagnostics confirmed in `report.rs`

`--report` emits a JSON summary with:

-   FastRoute version and input path.
-   Load, route, total and optional diagnosis timings.
-   Board statistics: layers, components, nets, pins, connections,
    unrouted count, ignored-class unrouted count, violations, unfixable
    violations, vias, blind/buried vias, traces and trace length.
-   Router/optimizer scores and related details.
-   Every unrouted connection, including net, endpoint item details,
    endpoint coordinates and layer information.
-   Clearance violations, layer, location, required/actual clearance,
    unfixable flag and involved items.

With `--diagnose`, the report clones the loaded board and attempts to
route each unrouted connection independently. If either endpoint can
route alone, the connection is classified as `congestion`; if neither
can, it is classified as `blocked` by geometry/rules. It also performs
in-context attempts corresponding to passes 1 and 10, reporting
state/details, ripped-item count and unrouted counts before/after/tail
removal.

Important diagnostic nuance: for nets with more than two pins, the
individual attempt connects an endpoint to the nearest part of its net,
not necessarily the other endpoint of the displayed airline. The MCP
description must preserve this caveat and must not overstate diagnosis
as a proof of root cause.

### 2.5 Existing KiCad bridge confirmed in `core.py` and `route_cli.py`

`integrations/kicad/plugins/core.py` is a GUI-independent KiCad bridge
using the `pcbnew` module. It:

-   Exports a board to Specctra DSN.
-   Imports a Specctra SES session.
-   Computes KiCad's unconnected/ratsnest count where supported.
-   Refills zones.
-   Handles binary discovery and other export-preparation logic.

`route_cli.py` runs headless with KiCad's Python interpreter and
`pcbnew`. It supports output board path, clear/keep-existing, FastRoute
mode, max passes, threads, neckdown settings, minimum width handling,
zone refill, zones-as-planes, text keepouts, max time, clearance margin,
obstacle zones, explicit FastRoute binary, work directory, export-only,
stitching-via preservation and live view. The Python `core.Router`
wrapper runs the binary, parses stderr progress, imports the SES and
marks a partial result if the child exits abnormally while a best SES
exists.

The KiCad README states that SES import replaces tracks and vias. The
headless script defaults to overwriting the input board unless `-o` is
supplied. MCP must therefore always use a distinct output board path and
must never rely on that default.

### 2.6 Not verified / must be checked during implementation

The public source inspection did not establish:

-   Exact runtime JSON schema for every field in the report; coding
    agent must capture real report fixtures and define a versioned
    normalized schema.
-   A stable machine-readable CLI status/progress mode. Current progress
    is mostly human-readable stderr plus live SSE.
-   Whether `pause` exists. It does not appear in the inspected CLI; do
    not expose it.
-   A durable resume token or in-memory job handle. Restart uses
    `--initial-session=<ses>` with a new process.
-   Whether the live SSE backlog can be replayed losslessly; bounded
    queues explicitly drop frames for slow clients.
-   Full KiCad DRC invocation details across supported KiCad
    versions/platforms. Verify installed `kicad-cli` syntax at runtime.
-   Whether every possible `--router.*` setting is safe to expose. Use
    an allowlist and schema validation.
-   Exact output of `--help` for each binary version. Always query the
    selected binary and store its version/help fingerprint.
-   Whether the fork's current source is byte-for-byte identical to
    upstream at any later commit. Implement against the checked-out
    fork, not upstream assumptions.

**Implementation gate:** before coding, clone the exact fork and record
`git rev-parse HEAD`, `git status --short`, `fastroute --version`,
`fastroute --help`, KiCad version and the Python `pcbnew` version. Add
this evidence to `docs/mcp/REPOSITORY_AUDIT.md`.

------------------------------------------------------------------------

## 3. Recommended architecture

Use a Python MCP server and a separate job manager. The server must not
run a routing subprocess inside the MCP request handler until
completion.

``` text
AI Agent / MCP Client
        |
        | MCP stdio (MVP)
        v
+-----------------------------+
| FastRoute MCP Server        |
|                             |
| Tools / Resources / Prompts |
| Input validation            |
| Approval gates              |
+--------------+--------------+
               |
               v
+-----------------------------+
| FastRoute Service Layer     |
|                             |
| Board workspace manager     |
| Job manager                 |
| Process supervisor          |
| Report normalizer           |
| Event collector             |
| KiCad bridge adapter        |
| DRC adapter                 |
+----------+------------------+
           |
      +----+-----------------------+
      |                            |
      v                            v
+-------------------+     +----------------------+
| FastRoute CLI     |     | KiCad Python/CLI     |
| Rust process      |     | pcbnew + kicad-cli   |
| .dsn -> .ses      |     | export/import/DRC    |
| --report          |     +----------------------+
| --diagnose        |
| --live SSE        |
+-------------------+
           |
           v
+------------------------------+
| Job Workspace + SQLite       |
| immutable input snapshot     |
| command/config manifest      |
| stdout/stderr log            |
| SSE-derived event history    |
| best SES checkpoint          |
| final report + DRC report    |
| routed output board          |
+------------------------------+
```

### 3.1 Process boundaries

-   **MCP process:** protocol handling and validation only.
-   **Service layer:** owns jobs, workspaces, persistence and process
    supervision.
-   **FastRoute process:** one isolated child process per active routing
    job.
-   **KiCad bridge:** separate subprocess using KiCad's own Python
    environment when available.
-   **SQLite:** durable state, event sequence, run metadata and artifact
    paths.

MVP can run all components on one workstation. Do not require Docker.

### 3.2 Proposed source layout

``` text
fastroute-mcp/
  pyproject.toml
  README.md
  src/fastroute_mcp/
    __init__.py
    server.py
    config.py
    schemas.py
    errors.py
    paths.py
    security.py
    service/
      board_service.py
      job_manager.py
      process_supervisor.py
      event_collector.py
      report_parser.py
      profile_manager.py
      comparison.py
      kicad_adapter.py
      drc_adapter.py
      persistence.py
    mcp/
      tools_board.py
      tools_routing.py
      tools_diagnostics.py
      tools_results.py
      resources.py
      prompts.py
    models/
      board.py
      job.py
      event.py
      report.py
  tests/
    unit/
    integration/
    fixtures/
      minimal_2layer/
      4layer_mixed_signal/
      differential_pair/
      dense_bga/
      known_unroutable/
  docs/
    REPOSITORY_AUDIT.md
    MCP_API.md
    SECURITY.md
```

Use the official Python MCP SDK. Pin versions. Use Pydantic models (or
equivalent strict validation) for all public tool inputs/outputs. Use
`asyncio.create_subprocess_exec`, never shell command concatenation.

------------------------------------------------------------------------

## 4. Job model and lifecycle

### 4.1 State machine

Use these persisted states:

-   `created`
-   `queued`
-   `preparing`
-   `running`
-   `stop_requested`
-   `finalizing`
-   `completed`
-   `completed_partial`
-   `stopped`
-   `failed`
-   `cancelled_before_start`

Do not use `paused`.

``` text
created -> queued -> preparing -> running
                                  |   |
                                  |   +-> stop_requested -> finalizing -> stopped
                                  |   +-> finalizing -> completed
                                  |   +-> finalizing -> completed_partial
                                  |   +-> failed
                                  |
                                  +-> cancelled_before_start (if cancelled while queued)
```

A process exit code alone does not determine success. Combine: 1. exit
code; 2. report existence and parse validity; 3. SES checkpoint
existence and parse/import validity; 4. report's unrouted/violation
counts; 5. cancellation/timeout request state.

### 4.2 Job identity and immutable inputs

Each job must include:

``` json
{
  "job_id": "fr_20261123_001",
  "created_at": "RFC3339 timestamp",
  "state": "running",
  "board_path": "workspace/input/board.kicad_pcb",
  "board_sha256": "...",
  "dsn_path": "workspace/input/board.dsn",
  "ses_path": "workspace/output/best.ses",
  "report_path": "workspace/output/report.json",
  "config_hash": "...",
  "fastroute_version": "0.1.14",
  "fastroute_binary": "/absolute/path/to/fastroute",
  "kicad_version": "...",
  "parent_job_id": null,
  "attempt_index": 1
}
```

The original board is copied or snapshotted into the job workspace
before export. Record the source file hash. Never overwrite it.

### 4.3 Workspace layout

``` text
~/.local/share/fastroute-mcp/jobs/<job_id>/
  manifest.json
  input/
    source.kicad_pcb
    board.dsn
    rules_snapshot/
  config/
    effective_config.json
    args.txt
    tune.txt
    pairs.txt
  logs/
    stdout.log
    stderr.log
    events.jsonl
  output/
    best.ses
    report.json
    routed.kicad_pcb
    drc_report.json
  tmp/
```

On Windows use `%LOCALAPPDATA%/FastRouteMCP/jobs`; allow explicit
workspace root configuration.

### 4.4 Start operation behavior

`start_routing` must:

1.  Validate the board path is within configured allowed roots.
2.  Verify file exists, extension is supported and size is within
    configured limits.
3.  Copy the board into a unique workspace; calculate SHA-256.
4.  Use KiCad's `route_cli.py --export-only` or the existing Python
    bridge to create a DSN. Do not invent a second DSN exporter.
5.  Preserve generated DSN and generated args for reproducibility.
6.  Validate profile and options against allowlists.
7.  Build argv as a list; do not invoke a shell.
8.  Start FastRoute with `-de`, `-do`, `--report`, optionally
    `--diagnose`, `--max-time`, `--live=<port>` and validated settings.
9.  Capture stdout and stderr concurrently to files and parse logs.
10. If live monitoring is enabled, connect to the FastRoute loopback
    `/events` SSE endpoint.
11. Return immediately with `job_id`, state and workspace artifact
    references.

The subprocess must have a configured timeout and resource limits where
practical. Avoid starting duplicate jobs for the same board/config
unless explicitly requested.

### 4.5 Stop and resume semantics

`stop_routing` requests graceful stop: - Preferred: send one graceful
termination signal supported by the platform (SIGINT on POSIX;
CTRL_BREAK_EVENT on Windows if launched in a new process group). - Use a
configurable grace period. - Do not send a second signal by default
because FastRoute's second signal exits immediately. - If the child
fails to stop after grace timeout, report that graceful stop timed out;
only force-kill with explicit `force=true` approval. - Wait for process
exit and finalization. - Validate that the best SES exists and is stable
before marking a partial result usable.

**Resume is a new attempt, not continuation of the same process.**
`resume_routing` must: 1. Require a completed/stopped/partial prior job.
2. Require the same input board hash, or explicit approval to use a new
board snapshot. 3. Use the prior job's best SES as
`--initial-session=<ses>`. 4. Create a new job ID and parent link. 5.
Reapply the same immutable electrical rules and record any changed
non-electrical routing parameters. 6. Never describe this as
pause/resume.

------------------------------------------------------------------------

## 5. Structured monitoring and events

### 5.1 Existing event source

Prefer FastRoute's existing `--live=<port>` SSE stream. Its current
events are `board`, `frame`, `tick`, `pass`, `stage`, `log`, `done`. The
current SSE is best-effort and has bounded event queues. Store events as
received, but derive final status from process completion and the final
JSON report.

If the SSE stream is unavailable, continue monitoring stdout/stderr and
poll job process state. Do not fail the routing job merely because the
viewer port could not be bound.

### 5.2 Normalized event envelope

Persist a normalized envelope independent of raw SSE payload:

``` json
{
  "schema_version": 1,
  "event_id": 123,
  "job_id": "fr_20261123_001",
  "type": "routing.pass_completed",
  "source": "fastroute_sse",
  "occurred_at": "RFC3339",
  "received_at": "RFC3339",
  "payload": {
    "pass": 4,
    "kind": "router",
    "elapsed_seconds": 12.8,
    "unrouted": 17,
    "violations": 0,
    "score": 923.4
  }
}
```

Map source events:

  Raw event               Normalized type
  ----------------------- ----------------------------
  `board`                 `board.loaded`
  `stage`                 `routing.stage_changed`
  `tick`                  `routing.progress`
  `pass` kind router      `routing.pass_completed`
  `pass` kind optimizer   `optimizer.pass_completed`
  `log`                   `process.log`
  `done`                  `routing.live_stream_done`

Also emit service-side events: `job.queued`, `job.started`,
`job.stop_requested`, `job.process_exited`, `job.report_parsed`,
`job.finalized`, `job.failed`, `artifact.created`.

### 5.3 Status polling

`get_routing_status` returns: - state; - elapsed time; - current
stage; - latest pass and progress counters if available; - latest known
unrouted count and violations; - best SES checkpoint metadata; - process
PID (local-only diagnostic, optional); - last event timestamp; - event
stream health; - report availability; - next recommended action.

Never fabricate an exact percentage if the engine has not supplied a
reliable denominator. Use `null` for unavailable values.

### 5.4 Event retention and backpressure

-   Persist all received compact events to JSONL and SQLite.
-   Do not persist every full `frame` geometry payload by default;
    frames can be large. Store only the latest frame or an optional
    bounded ring buffer.
-   Preserve `pass`, `stage`, `tick`, `log`, `done` events.
-   SSE queue gaps must be detectable where possible. If not, mark
    stream `best_effort`.
-   Status polling must remain useful without SSE.
-   Provide `get_routing_events(job_id, after_event_id, limit)` with
    bounded pagination.

------------------------------------------------------------------------

## 6. MCP API: tools

All tools return a consistent envelope:

``` json
{
  "ok": true,
  "data": {},
  "warnings": [],
  "error": null
}
```

On failure:

``` json
{
  "ok": false,
  "data": null,
  "warnings": [],
  "error": {
    "code": "JOB_NOT_FOUND",
    "message": "No job exists with the supplied ID.",
    "retryable": false,
    "details": {}
  }
}
```

MCP protocol errors should be reserved for invalid protocol/tool
invocation. Domain errors should be returned as structured tool results
so the agent can reason about them.

### 6.1 Board inspection tools

#### `inspect_board`

Input:

``` json
{
  "board_path": "string",
  "include": ["summary", "layers", "nets", "net_classes", "constraints"],
  "max_items": 500
}
```

Output:

``` json
{
  "board_id": "sha256:...",
  "format": "kicad_pcb",
  "kicad_version": "...",
  "summary": {
    "width_mm": 100.0,
    "height_mm": 80.0,
    "layers": 4,
    "components": 86,
    "nets": 120,
    "pins": 430,
    "tracks": 0,
    "vias": 0,
    "unconnected_count": 420
  },
  "layers": [],
  "net_classes": [],
  "constraints": [],
  "warnings": []
}
```

Use `pcbnew` via the configured KiCad Python interpreter or the existing
`core.py` helper. If only DSN is available, report that inspection is
DSN-derived and omit KiCad-only properties. Do not claim a full board
inspection if the adapter cannot extract a field.

#### `get_board_rules`

Input: `board_path`.

Output: board design rules, net-class clearances/widths, per-layer
widths, copper-edge/minimum-width constraints, differential-pair gaps,
length/skew rules, keepouts and a `source` field (`kicad_dru`, `pcbnew`,
`dsn`, or `unknown`). Unsupported rules must be listed as warnings.

#### `get_net_details`

Input: `board_path`, `net_names[]` (optional), `include_endpoints=true`.

Output: each requested net's class, endpoints (reference/pad when
available), layer range, constraints and whether it is ignored by
autorouter. For large boards, require pagination.

#### `get_differential_pairs`

Input: `board_path`.

Output: pair P/N net names, gap, per-layer gap, skew tolerance and
source rule. Do not infer a pair from naming alone unless explicitly
marked as heuristic.

### 6.2 Routing control tools

#### `start_routing`

Input:

``` json
{
  "board_path": "string",
  "profile": "balanced",
  "max_time_seconds": 600,
  "max_passes": null,
  "threads": null,
  "multi_start": 4,
  "diagnose": false,
  "live_monitoring": true,
  "existing_tracks": "router_default",
  "extra_options": {}
}
```

`existing_tracks` enum: - `router_default` - `keep_existing` -
`clear_unlocked`

The KiCad headless adapter supports explicit keep/clear semantics. For
the first implementation, prefer `router_default` only when the operator
knowingly accepts the current plugin behavior; show a warning because
unlocked tracks may be moved/rerouted. `keep_existing` and
`clear_unlocked` map to the verified `route_cli.py` options.

Output:

``` json
{
  "job_id": "fr_...",
  "state": "queued",
  "board_sha256": "...",
  "effective_profile": "balanced",
  "workspace": "opaque job reference",
  "warnings": []
}
```

This tool must return after the job is registered and started/queued,
not after routing completes.

#### `stop_routing`

Input:

``` json
{
  "job_id": "fr_...",
  "force": false,
  "grace_seconds": 60
}
```

Output: requested state, whether signal was sent, process state,
checkpoint existence and finalization state. `force=true` requires
explicit user approval and must never be defaulted by the agent.

#### `resume_routing`

Input:

``` json
{
  "source_job_id": "fr_...",
  "profile": "balanced",
  "max_time_seconds": 600,
  "multi_start": 4,
  "diagnose": false,
  "approved_board_hash": "optional exact hash"
}
```

Output: new job ID, `parent_job_id`, source SES path/hash, same/new
board hash and warnings. Internally uses `--initial-session`; it does
not revive the old process.

#### `get_routing_status`

Input: `job_id`.

Output includes the status fields defined in Section 5.3.

#### `get_routing_logs`

Input: `job_id`, `stream` (`stdout|stderr|combined`), `after_line`
(default 0), `limit` (max 500).

Output: lines, next cursor, truncation flag.

#### `get_routing_events`

Input: `job_id`, `after_event_id` (default 0), `limit` (max 500).

Output: normalized events, next cursor, stream health.

#### `list_routing_jobs`

Input: optional `board_sha256`, `state[]`, `limit`, `cursor`.

Output: summaries, total if known, next cursor.

### 6.3 Diagnostics tools

#### `get_routing_report`

Input: `job_id`.

Output: normalized report. Preserve raw report path and raw JSON
artifact. Include report schema version and parser version.

#### `get_unrouted_connections`

Input: `job_id`, optional `net_names[]`, `diagnosis_class`
(`all|congestion|blocked|ignored|unknown`), `limit`, `cursor`.

Output: net, endpoint metadata, XY coordinates in mm, layers, diagnosis
and diagnostic caveats.

#### `diagnose_unrouted_nets`

Input:

``` json
{
  "job_id": "fr_...",
  "run_diagnosis_if_missing": false
}
```

If diagnosis already exists, return it. If not, only launch a diagnostic
rerun if the user explicitly requested it or approved it. `--diagnose`
is a compute-intensive operation and should be a separate bounded
attempt using the same input and config snapshot.

#### `get_clearance_violations`

Input: `job_id`, optional `include_unfixable`.

Output: required/actual clearance, location, layer, items and unfixable
flag.

#### `compare_routing_runs`

Input:

``` json
{
  "job_ids": ["fr_a", "fr_b"],
  "metrics": ["unrouted", "violations", "vias", "trace_length_mm", "route_time_s", "router_score", "optimizer_score"]
}
```

Output: aligned metrics, deltas, best run per metric and a
`quality_warning` if board hashes or rule snapshots differ. Never
declare a winner using a single score when hard DRC or constraint
regressions exist.

### 6.4 Configuration tools

#### `get_routing_profiles`

Input: none.

Output: available profile names, descriptions, parameter values and
profile schema version.

#### `validate_routing_config`

Input: profile and requested options.

Output: normalized effective config, rejected settings, warnings and
whether approval is required. Must not launch a job.

#### `get_routing_config`

Input: `job_id` or profile name.

Output: complete effective config, CLI argv (redact local secrets;
normally none), FastRoute version and config hash.

#### `apply_routing_profile`

Do not mutate a running job. Input: `profile`, optional `overrides`.
Output: validated config object or an approval requirement. The
resulting config is supplied to a later
`start_routing`/`resume_routing`.

### 6.5 KiCad result tools

#### `validate_routing_result`

Input:

``` json
{
  "job_id": "fr_...",
  "run_kicad_drc": true,
  "refill_zones": true
}
```

Run only after a routed KiCad output board exists. Use the installed
`kicad-cli pcb drc --help` to select version-compatible invocation. Save
full DRC output and normalize violations. Return router report metrics
and KiCad DRC metrics separately.

#### `export_routing_artifacts`

Input: `job_id`, `artifacts[]` (`ses`, `report`, `routed_board`,
`drc_report`, `logs`, `manifest`).

Output: exact artifact paths and SHA-256 hashes. Do not return arbitrary
file-system paths outside the configured job root.

#### `import_routing_result`

This tool is high-impact. It must not modify the source board in place.
Require `output_board_path` under an allowed output root, an explicit
approval token/confirmation and source hash match. Default behavior is
to create a new routed board file. Return output hash and KiCad ratsnest
count.

------------------------------------------------------------------------

## 7. MCP resources

Expose read-only resources with stable URIs:

  -----------------------------------------------------------------------------
  URI                                       Content
  ----------------------------------------- -----------------------------------
  `fastroute://capabilities`                Detected binary/version/features
                                            and KiCad adapter availability

  `fastroute://profiles`                    Current routing profiles

  `fastroute://jobs`                        Recent job summaries

  `fastroute://jobs/{job_id}/status`        Current status

  `fastroute://jobs/{job_id}/report`        Normalized final report

  `fastroute://jobs/{job_id}/diagnostics`   Unrouted and violation diagnostics

  `fastroute://jobs/{job_id}/events`        Recent event tail; paginate via
                                            tool for larger history

  `fastroute://jobs/{job_id}/manifest`      Reproducibility manifest
  -----------------------------------------------------------------------------

Resources are snapshots; do not promise push subscriptions or
notifications until implemented and tested. Use tools for pagination and
actions.

------------------------------------------------------------------------

## 8. MCP prompts

Register reusable user-invoked prompts:

### `analyze_board_routability`

Inspect board layers, rules, net classes, differential pairs,
constraints and likely routing complexity. Return facts, warnings, and
missing information. Do not start routing.

### `route_board_safely`

Use an approved routing profile. Preserve all electrical constraints.
Explain existing-track handling before starting if it may move unlocked
tracks. Start routing and return the job ID.

### `monitor_routing_job`

Poll job status and inspect event history. Summarize meaningful
improvements, stagnation and errors. Do not stop a job unless the user
authorized a stop policy.

### `diagnose_unrouted_connections`

Read the final report and explain congestion vs blocked classifications,
ignored nets and multi-pin caveats. Recommend which problems are
router-parameter candidates versus PCB-design issues.

### `improve_routing_completion`

Compare previous runs. Propose a bounded next attempt. Never relax
clearance, minimum width, differential gap, impedance constraints,
keepouts or layer restrictions. Request approval before changing any
rule or board design data.

### `review_routing_quality`

Compare unrouted connections, KiCad DRC, vias, trace length, pair
skew/length constraints and report warnings. Explain trade-offs rather
than optimizing one scalar score.

------------------------------------------------------------------------

## 9. Routing profiles and parameter policy

Profiles are MCP-layer presets, not claims that FastRoute ships these
named presets. Keep profile definitions in versioned YAML/JSON and
persist the resolved values per job.

Suggested initial profiles:

  -----------------------------------------------------------------------
  Profile                 Intent                  Suggested behavior
  ----------------------- ----------------------- -----------------------
  `quick`                 Fast feasibility check  Use route_cli quick
                                                  mode / router-only;
                                                  modest max time

  `balanced`              Default                 FastRoute default
                                                  improvements, bounded
                                                  time, normal optimizer

  `quality`               Better optimization     Longer budget,
                                                  optimizer enabled,
                                                  bounded multi-start

  `dense_board`           Congested designs       Longer budget and
                                                  controlled multi-start;
                                                  do not relax rules

  `parity`                Reproducibility against `--parity`,
                          Freerouting             deterministic
                                                  compatibility mode
  -----------------------------------------------------------------------

Only expose documented and validated settings initially: - max time; -
max passes; - autorouter threads; - optimizer threads; - multi-start
count; - optimizer enabled; - parity mode; - `--diagnose`; - existing
tracks mode; - explicit pair/tune files generated from board rules.

### Never expose as routine autonomous controls

-   clearance values;
-   copper-to-edge clearance;
-   minimum track width;
-   differential-pair gap/skew;
-   controlled impedance widths;
-   net-class ignore lists;
-   keepout removal;
-   layer restrictions;
-   board geometry changes.

These are design constraints, not ordinary optimization knobs. If the
user explicitly requests a rule change, show the before/after values and
require confirmation before generating a new board snapshot. The agent
may recommend a change but cannot apply it silently.

Reject unknown `--router.*` keys. Do not allow raw arbitrary CLI
fragments from the agent. Parse settings into a typed allowlist and
construct argv internally.

------------------------------------------------------------------------

## 10. Iterative optimization supervisor

Implement the supervisor as an explicit bounded workflow, not a hidden
autonomous loop.

For each iteration:

1.  Confirm source board hash and immutable rule snapshot.
2.  Start a new job with selected config and optional prior SES
    checkpoint.
3.  Monitor status and collect the final report.
4.  Run KiCad DRC on the generated routed board when available.
5.  Compare with the baseline and all prior attempts.
6.  Determine whether another attempt is justified.
7.  Ask for approval before launching additional attempts unless the
    user has explicitly authorized a bounded multi-run policy.

### Candidate decision rules

-   If `unrouted == 0` and KiCad DRC has no new violations, mark routing
    candidate complete.
-   If unrouted decreases and violations do not increase, the run is a
    candidate improvement.
-   If `unrouted` stagnates across two attempts, inspect diagnostics
    before another run.
-   If all remaining are `blocked`, recommend PCB design review rather
    than endless retries.
-   If remaining are mostly `congestion`, a new multi-start order or
    longer bounded run may be worth testing.
-   If report has ignored-class unrouted connections, report them
    separately; do not treat them as ordinary routing failures.
-   If KiCad DRC detects new unconnected items or violations, do not
    finalize even if FastRoute's own report looks good.

No automatic changes to placement, routing constraints or board geometry
in this project scope.

------------------------------------------------------------------------

## 11. KiCad export/import and DRC workflow

### 11.1 Preferred integration

Use the repository's existing `integrations/kicad/plugins/route_cli.py`
and `core.py` rather than duplicating their DSN export/session import
logic.

MVP should invoke the script with KiCad's Python interpreter:

``` text
<kicad-python> route_cli.py <input.kicad_pcb>
  -o <workspace/output/routed.kicad_pcb>
  --work-dir <workspace>
  --max-time <seconds>
  --live
  -- <validated FastRoute options>
```

However, `route_cli.py` currently wraps the whole operation. For maximum
process lifecycle control and access to `--report`/`--diagnose`,
evaluate two implementation modes:

**Mode A (preferred MVP if feasible):** use `core.py` export helpers to
create DSN, run FastRoute CLI directly with `--report`, then use
`core.py` import/refill helpers to create the output board. This keeps
the Rust process directly supervised.

**Mode B:** use `route_cli.py` as a subprocess for export, routing and
import. If chosen, modify it minimally to expose report/diagnose paths
and reliable structured result metadata. Do not assume its current
command-line wrapper emits all the data required by MCP.

The coding agent must inspect `core.Router` methods and subprocess
behavior before selecting A or B. Keep the decision in
`REPOSITORY_AUDIT.md`.

### 11.2 Required safe file flow

``` text
source.kicad_pcb (read-only)
        |
        v
workspace/input/snapshot.kicad_pcb
        |
        v
KiCad export -> board.dsn
        |
        v
FastRoute -> best.ses + report.json
        |
        v
KiCad import into in-memory snapshot copy
        |
        v
refill zones -> routed.kicad_pcb
        |
        v
KiCad CLI DRC -> drc_report
```

Always use a separate output board path. Never overwrite original PCB in
the MVP.

### 11.3 DRC acceptance

A final result must report separately: - FastRoute unrouted
connections. - FastRoute clearance violations (including
unfixable/pre-existing). - KiCad ratsnest/open connection count after
SES import and zone refill. - KiCad DRC violations grouped by
type/severity. - Any rules the plugin could not carry into DSN, as
warnings.

Do not collapse these into one "DRC clean" boolean without supporting
detail.

------------------------------------------------------------------------

## 12. Persistence and reproducibility

Use SQLite with migrations. Suggested tables:

-   `jobs`: job metadata/state/timestamps/parent job/source hash/config
    hash.
-   `events`: normalized events with monotonic `event_id`.
-   `artifacts`: artifact type/path/hash/size/created time.
-   `profiles`: versioned profile definitions.
-   `approvals`: approval requests and decisions.
-   `job_metrics`: normalized report metrics.
-   `schema_migrations`: database schema version.

Persist a manifest containing:

-   repository URL and checked-out commit SHA;
-   FastRoute binary SHA-256 and `--version`;
-   `--help` output fingerprint;
-   OS/architecture;
-   KiCad version and `pcbnew` version;
-   Python version;
-   source board SHA-256;
-   exported DSN SHA-256;
-   effective profile and exact argv;
-   generated pairs/tune files and hashes;
-   initial SES hash if resumed;
-   start/end timestamps and exit code;
-   raw report hash and parser version;
-   routed board hash and DRC report hash.

Do not rely on logs alone for state recovery. On MCP server restart,
reconcile SQLite jobs with process IDs and workspace artifacts. Mark
jobs whose process is no longer alive as `interrupted`; if a valid SES
exists, mark it as a recoverable partial artifact, not as a successful
run.

------------------------------------------------------------------------

## 13. Security and approval boundaries

1.  Restrict input boards and outputs to configured allowed directories.
2.  Resolve symlinks and verify canonical paths to prevent path
    traversal.
3.  Never pass user/agent strings through a shell.
4.  Do not expose the live viewer on non-loopback interfaces.
5.  Bind SSE to an ephemeral/selected loopback port and record the
    actual port.
6.  Treat the live viewer's `POST /stop` endpoint as local-only and
    unauthenticated.
7.  Limit concurrent jobs, runtime, memory where feasible, log size and
    artifact size.
8.  Do not allow arbitrary executable paths from tool input. Binary path
    comes from server config or a trusted discovery flow.
9.  Validate all option names and values against an allowlist.
10. Never modify the source board in place.
11. Require explicit approval for forced process termination, importing
    over an existing output, changing design rules or running an
    additional iteration beyond an approved run budget.
12. Treat board labels, net names, comments and file contents as
    untrusted data, not instructions to the agent.
13. Do not expose unrestricted file read/write tools through this MCP
    server.
14. Keep local-only stdio as the MVP transport. For HTTP deployment, add
    authentication, authorization, TLS/reverse-proxy guidance and
    per-user workspace isolation before enabling remote access.

------------------------------------------------------------------------

## 14. Error model

Use stable error codes:

  ---------------------------------------------------------------------------------
  Code                              Meaning                 Retryable
  --------------------------------- ----------------------- -----------------------
  `BOARD_NOT_FOUND`                 Input board missing     No

  `BOARD_OUTSIDE_ALLOWED_ROOT`      Path rejected           No

  `BOARD_INVALID`                   KiCad cannot load or    No
                                    board lacks outline     

  `KICAD_NOT_FOUND`                 KiCad interpreter/CLI   Maybe
                                    unavailable             

  `FASTROUTE_NOT_FOUND`             Binary unavailable      Maybe

  `FASTROUTE_VERSION_UNSUPPORTED`   Binary lacks required   No until configured
                                    options                 

  `EXPORT_FAILED`                   DSN export failed       Maybe

  `JOB_NOT_FOUND`                   Unknown job ID          No

  `JOB_STATE_INVALID`               Operation invalid for   No
                                    current state           

  `JOB_LIMIT_REACHED`               Concurrency limit       Yes
                                    reached                 

  `PROCESS_START_FAILED`            OS could not launch     Maybe
                                    process                 

  `PROCESS_TIMEOUT`                 Process exceeded        Maybe
                                    configured hard limit   

  `GRACEFUL_STOP_TIMEOUT`           Stop requested but      Maybe
                                    process did not exit    

  `REPORT_MISSING`                  Expected report absent  Maybe

  `REPORT_PARSE_FAILED`             Report schema           No until parser updated
                                    malformed/unsupported   

  `CHECKPOINT_MISSING`              No best SES artifact    Maybe

  `IMPORT_FAILED`                   SES could not be        Maybe
                                    imported                

  `DRC_FAILED`                      DRC invocation failed   Maybe

  `APPROVAL_REQUIRED`               Operation requires      No
                                    human confirmation      

  `CONFIG_REJECTED`                 Invalid/unsafe config   No

  `EVENT_STREAM_UNAVAILABLE`        SSE could not be        Yes; job may continue
                                    connected               

  `ARTIFACT_NOT_FOUND`              Artifact missing        No
  ---------------------------------------------------------------------------------

Never report a job as successful solely because the child process
returned exit code 0.

------------------------------------------------------------------------

## 15. Phased implementation plan

Each phase must be independently testable and merged only when its
acceptance criteria pass.

### Phase 0 --- Exact repository audit and fixtures

**Tasks** - Clone the exact `aviyeola/fastroute` fork. - Record commit
SHA, dirty state, binary version/help and platform. - Inspect
`core.Router`, `route_cli.py`, `report.rs`, `live.rs`, CLI exit handling
and final report schema. - Capture real `--report` JSON from at least
one small test board, both with and without `--diagnose`. - Capture raw
SSE event examples for every event type. - Record KiCad Python and CLI
versions. - Create a repository audit document and sanitized fixtures.

**Acceptance** - Audit document names every verified integration
point. - Fixture report parses with a small standalone parser. - SSE
capture includes `stage`, `tick`, `pass`, `log`, `done` where the
selected fixture exercises them. - No implementation assumption remains
about pause/resume: explicitly documented as unsupported in MVP.

### Phase 1 --- MCP skeleton and capability discovery

**Tasks** - Create Python package and MCP stdio server. - Add strict
Pydantic schemas and common error envelope. - Implement
`fastroute://capabilities`. - Discover trusted FastRoute binary, KiCad
Python and `kicad-cli` from configuration. - Capture version/help
fingerprints. - Add `inspect_board` and `get_board_rules` using existing
KiCad bridge.

**Acceptance** - MCP Inspector can connect over stdio. - Tool schemas
validate malformed input and reject unknown fields/options. - Capability
resource correctly reports missing dependencies. - Inspection works on a
known KiCad fixture without modifying it. - Paths outside allowed roots
are rejected.

### Phase 2 --- Workspace and asynchronous job manager

**Tasks** - Implement workspace creation, board snapshot, hashing and
manifest. - Implement SQLite migrations and job state machine. -
Implement process supervisor using `asyncio.create_subprocess_exec`. -
Use separate stdout/stderr readers. - Implement `start_routing`,
`get_routing_status`, `get_routing_logs`, `list_routing_jobs`. - Invoke
FastRoute directly on DSN in a controlled workspace. - Ensure report and
best SES output paths are unique per job.

**Acceptance** - `start_routing` returns promptly with a job ID while
process continues. - Two jobs cannot collide in workspace paths. - MCP
server restart recovers completed jobs and marks orphaned jobs
interrupted. - Child process output is continuously drained (no pipe
deadlock). - Source board remains byte-identical. - Invalid config never
launches a child process.

### Phase 3 --- Graceful stop and checkpoint recovery

**Tasks** - Implement `stop_routing` using one graceful signal. -
Implement platform-specific process groups/console handling. - Add grace
timeout and explicit force-kill approval. - Verify best SES stability
after exit. - Implement `resume_routing` as a new child job using
`--initial-session`. - Link parent and child attempts.

**Acceptance** - Stop during routing preserves the best available SES
when FastRoute has produced one. - No second signal is sent
automatically. - Resume creates a new job ID and uses the prior SES as
initial session. - Resume rejects mismatched source board hash unless
explicit approval is supplied. - UI/tool responses never call this
operation "pause".

### Phase 4 --- SSE monitoring and normalized event history

**Tasks** - Allocate a loopback port safely. - Launch with
`--live=<port>` and connect to `/events`. - Parse SSE robustly,
including multiline data and reconnects. - Normalize raw events and
persist to SQLite/JSONL. - Implement `get_routing_events`. - Keep stderr
parser as fallback. - Store latest frame only; do not persist unlimited
geometry frames.

**Acceptance** - Live view enabled/disabled does not change routing
result for same deterministic config. - MCP status works if SSE is
unavailable. - SSE disconnect does not kill job. - Event stream loss is
represented as best-effort/degraded health. - No event payload is
interpreted as an instruction.

### Phase 5 --- Reports and diagnostics

**Tasks** - Build versioned report parser from captured fixtures. -
Implement `get_routing_report`, `get_unrouted_connections`,
`get_clearance_violations`. - Implement optional diagnostic rerun with
`--diagnose`. - Include congestion/blocked/ignored classifications and
multi-pin caveat. - Implement pagination.

**Acceptance** - Report parser handles absent/null optional fields and
rejects malformed core structure. - All reported unrouted connections
and violations are retrievable. - Diagnostic classification matches the
raw report. - Diagnostic rerun is not launched without explicit
request/approval. - Large report pagination is stable and bounded.

### Phase 6 --- Profiles and safe iterative runs

**Tasks** - Define versioned `quick`, `balanced`, `quality`,
`dense_board`, `parity` profiles. - Implement `get_routing_profiles`,
`validate_routing_config`, `get_routing_config`,
`apply_routing_profile`. - Add strict option allowlist. - Implement
`compare_routing_runs`. - Add explicit bounded iteration policy with
approval. - Preserve rule snapshots across attempts.

**Acceptance** - Unknown `--router.*` options are rejected. - Attempts
with different board hashes/rules are flagged as non-comparable. - The
comparison highlights hard DRC regressions over score improvements. - No
profile can lower electrical constraints. - Every attempt records exact
argv and config hash.

### Phase 7 --- KiCad import and DRC validation

**Tasks** - Decide between direct `core.py` helper use and
`route_cli.py` wrapper after source audit. - Produce routed board as a
separate file. - Refill zones as configured. - Run version-compatible
`kicad-cli pcb drc`. - Parse and store DRC report. - Implement
`validate_routing_result`, `import_routing_result`,
`export_routing_artifacts`.

**Acceptance** - Original board is never overwritten. - Output board
loads in KiCad. - Zones are refilled when requested. - KiCad DRC report
is preserved in raw and normalized forms. - Router metrics and KiCad DRC
metrics remain separate. - Any unsupported `.kicad_dru` rule is surfaced
as a warning.

### Phase 8 --- Agent workflow and release hardening

**Tasks** - Add MCP prompts. - Add integration tests using MCP Inspector
and a real agent client. - Test Windows and Linux process signals. - Add
resource limits, cleanup/retention policy and recovery. - Document stdio
configuration for local clients. - Add Streamable HTTP only as a
separate optional deployment profile with auth.

**Acceptance** - An agent can inspect, route, monitor, stop,
resume-from-checkpoint, diagnose and compare runs without shell
access. - The agent cannot modify board rules through routine tools. - A
complete end-to-end fixture run produces a separate routed board and DRC
report. - Repeated runs are reproducible from manifests. - Security
tests cover path traversal, shell injection, arbitrary executable
selection and unauthorized destructive actions.

------------------------------------------------------------------------

## 16. Test matrix and fixture strategy

Use small deterministic boards first; avoid using a large production
design as the first integration test.

  Fixture                 Test purpose
  ----------------------- ---------------------------------------------
  `minimal_2layer`        Basic export, route, report and import
  `4layer_mixed_signal`   Layer selection and net classes
  `differential_pair`     Pair file, gap and skew reporting
  `dense_bga`             Congestion and multi-start behavior
  `known_unroutable`      Blocked/congestion diagnostic cases
  `preexisting_tracks`    keep-existing vs router-default behavior
  `zone_heavy`            Zone removal/refill and ratsnest validation
  `preexisting_drc`       Separate unfixable/pre-existing violations
  `length_matching`       Tune file and post-route length metrics

Automated tests:

-   Unit: path validation, config validation, state transitions, report
    parsing, SSE parsing, event normalization, comparison calculations.
-   Process integration: normal completion, graceful stop, hard timeout,
    child crash, missing report, corrupt SES, MCP server restart.
-   KiCad integration: DSN export, SES import, zone refill, DRC.
-   Security: shell metacharacters in board/net names, path traversal,
    symlinks, arbitrary CLI options, force kill without approval.
-   Regression: same board/config/version produces stable metrics; exact
    byte equality only when parity mode and its required deterministic
    conditions are used.

------------------------------------------------------------------------

## 17. Recommended MVP

Deliver only the following first:

**MCP tools** 1. `inspect_board` 2. `get_board_rules` 3. `start_routing`
4. `get_routing_status` 5. `get_routing_logs` 6. `stop_routing` 7.
`get_routing_report` 8. `get_unrouted_connections` 9.
`diagnose_unrouted_nets` 10. `resume_routing`

**Resources** - `fastroute://capabilities` - `fastroute://jobs` -
`fastroute://jobs/{id}/status` - `fastroute://jobs/{id}/report`

**Infrastructure** - Python MCP stdio server. - Async subprocess
supervisor. - Isolated job workspace. - SQLite job registry. - FastRoute
`--report`, `--diagnose`, `--initial-session`, `--max-time`. - Existing
live SSE, treated as best-effort. - No direct board mutation; no remote
HTTP server.

**MVP demo acceptance:** An agent inspects a KiCad board, starts a route
job, receives a job ID immediately, monitors pass/status/log events,
gracefully stops if requested, reads the final report, diagnoses
remaining nets, and starts a new attempt from the best SES checkpoint.
The original board remains unchanged throughout.

------------------------------------------------------------------------

## 18. Prioritized backlog

### P0 --- Required for MVP

-   Exact fork audit and real fixtures.
-   MCP stdio server and schemas.
-   Board snapshot and safe workspace.
-   Async process lifecycle.
-   Status/log/report/stop.
-   Checkpoint recovery via new job and `--initial-session`.
-   Path and CLI-option allowlists.

### P1 --- High value

-   SSE monitoring and event history.
-   Full unrouted-net diagnostics.
-   KiCad output board import and DRC.
-   Profiles and run comparison.
-   Persistent recovery after MCP restart.

### P2 --- Advanced

-   Bounded autonomous iteration supervisor.
-   Richer board/net inspection resources.
-   Optional visualization resource linking to the existing live viewer.
-   Streamable HTTP with authentication.
-   Optional Rust-native structured event output if stderr/SSE parsing
    proves insufficient.

### P3 --- Only if justified by measured limitations

-   Native Rust library/API instead of CLI orchestration.
-   Native pause/resume if the engine can support safe suspension
    semantics.
-   Fine-grained per-net live controls.
-   Automatic placement/geometry suggestions (separate
    approval-controlled system).

------------------------------------------------------------------------

## 19. Definition of done

The project is complete when:

1.  A local MCP client can discover and invoke the tools.
2.  A job is asynchronous, persistent and independently identifiable.
3.  The agent can monitor progress without scraping the browser UI.
4.  The agent can stop safely and resume from the best SES checkpoint as
    a new attempt.
5.  Diagnostics and reports are parsed into typed structures.
6.  Multiple attempts can be compared against the same board/rule
    snapshot.
7.  A routed board is exported to a new file and validated with KiCad
    DRC.
8.  No routine tool can silently relax electrical constraints or
    overwrite the original board.
9.  The implementation works on the supported developer OS with explicit
    dependency/version reporting.
10. Every claimed FastRoute capability is backed by a source reference
    or a fixture test.

------------------------------------------------------------------------

## 20. Source references used for this audit

The following source files were inspected on the `main` branch of the
supplied fork:

-   Repository page and root README:
    https://github.com/aviyeola/fastroute
-   Workspace manifest:
    https://raw.githubusercontent.com/aviyeola/fastroute/main/Cargo.toml
-   CLI implementation:
    https://raw.githubusercontent.com/aviyeola/fastroute/main/crates/fastroute/src/main.rs
-   Report and diagnostic implementation:
    https://raw.githubusercontent.com/aviyeola/fastroute/main/crates/fastroute/src/report.rs
-   Live HTTP/SSE implementation:
    https://raw.githubusercontent.com/aviyeola/fastroute/main/crates/fastroute/src/live.rs
-   KiCad integration README:
    https://raw.githubusercontent.com/aviyeola/fastroute/main/integrations/kicad/README.md
-   KiCad bridge:
    https://raw.githubusercontent.com/aviyeola/fastroute/main/integrations/kicad/plugins/core.py
-   Headless KiCad CLI:
    https://raw.githubusercontent.com/aviyeola/fastroute/main/integrations/kicad/plugins/route_cli.py

**Source-of-truth rule:** this specification describes the inspected
fork at the inspection date. The coding agent must re-check the current
checkout before implementation and update the audit if the branch has
changed.
