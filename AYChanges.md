# AYChanges

## Summary
Added a new Python MCP server (`fastroute-mcp/`) that wraps the existing Rust FastRoute CLI and the repository's KiCad bridge, implementing the orchestration layer specified in `FASTROUTE-MCP-ARCHITECTURE.md`. The Rust routing engine is unchanged; the MCP layer adds asynchronous job management, isolated workspaces, SQLite persistence, normalized reports/events, graceful stop/resume-from-checkpoint, profiles, diagnostics, comparison, and KiCad result validation hooks. Also added a repository audit document.

## Reasoning and design decisions

### 1. Keep FastRoute as a subprocess
Reasoning: The architecture explicitly forbids porting the routing engine or mutating Rust internals. FastRoute already provides `.dsn -> .ses`, `--report`, `--diagnose`, `--initial-session`, `--max-time`, signal handling and live SSE. Reusing these preserves engine behavior and minimizes risk.

### 2. Python MCP SDK 2.x
Reasoning: The installed environment provides `mcp` 2.3.0, where `FastMCP` was renamed to `MCPServer`. Implemented against `MCPServer` and stdio transport. Tool inputs/outputs use plain dict envelopes matching the specified `{ok,data,warnings,error}` shape.

### 3. CLI argument form
Discovery: FastRoute's parser only accepts `=`-joined forms for `--report`, `--max-time`, `--live`, `--multi-start`, `--initial-session`, `--max-time`, and `--router.*`; `-de`/`-do` accept a following value. Initial implementation used space-separated flags, which silently dropped `--report` and `--max-time`. Fixed `_build_argv` to use `--report=`, `--max-time=`, `--live=`, `--multi-start=`, `--initial-session=`, and `--router.autorouter.max_passes=`. This is required for report generation and time limits.

### 4. Report schema normalization
Discovery: Real `--report` JSON uses `fastroute`, `input`, `time_s`, `stats`, `unrouted`, `clearance_violations`, and `diagnosis` (when `--diagnose`). Each unrouted item uses `from`/`to`/`from_xy`/`to_xy`; diagnosis is a nested object with `class`. Updated `report_parser.py` to map these fields, including congestion/blocked classification. Also handles absent diagnosis gracefully.

### 5. Job lifecycle and state machine
Implemented states from the spec (created, queued, preparing, running, stop_requested, finalizing, completed, completed_partial, stopped, failed, cancelled_before_start, plus interrupted for recovery). Success is not inferred from exit code alone; finalization checks report/SES existence.

### 6. Graceful stop
Uses one platform signal (SIGINT on POSIX, CTRL_BREAK_EVENT on Windows with CREATE_NEW_PROCESS_GROUP). No second signal is sent automatically. Force-kill requires explicit `force=true`. Grace timeout reported as degraded state.

### 7. Resume as new job
`resume_routing` creates a new job ID, links `parent_job_id`, copies the prior best SES into the new workspace as `--initial-session`, and records the source hash. Never described as pause/resume.

### 8. SSE monitoring
Allocates a free loopback port, passes `--live=<port>`, connects to `/events` with httpx streaming, normalizes events into SQLite, stores only the latest frame, and degrades gracefully on disconnect. Status polling remains useful without SSE.

### 9. KiCad adapter
Discovery is best-effort; KiCad is not installed in this environment. Adapters report `KICAD_NOT_FOUND` and board inspection falls back to DSN-derived summaries. Export/import/DRC remain implemented through the repository's `core.py` and `kicad-cli` for environments where KiCad is available.

### 10. Security and validation
Path validation restricts inputs/outputs to configured roots, resolves symlinks, and rejects traversal. CLI options are allowlisted; unknown `--router.*` keys rejected. No shell invocation; argv built as a list. Source board is copied and never overwritten.

### 11. Testing
Verified end-to-end on Windows with the bundled `synth_rules.dsn` fixture: job starts and returns immediately, SSE events collected, graceful stop preserves SES, resume creates a child job, report parsing yields unrouted/violations/vias/trace length, diagnostics classify congestion vs blocked, manifest written. MCP stdio server exposes 23 tools and resources.

## Files added
- fastroute-mcp/ (Python package)
- docs/mcp/REPOSITORY_AUDIT.md
- AYChanges.md
- FASTROUTE-MCP-ARCHITECTURE.md (moved into repo)

## Known limitations
- KiCad-dependent features (board inspection, DRC, SES import) untested due to missing KiCad.
- Binary built in debug mode; release build recommended.
- Report parser covers observed schema; may need updates for additional FastRoute versions.
