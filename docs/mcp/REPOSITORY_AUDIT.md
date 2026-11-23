# Repository Audit

## Checkout
- Repository: https://github.com/aviyeola/fastroute
- Branch: main
- Commit SHA: 368a815bf15d6422f95521b661b408de8fbbaa86
- Dirty state at audit: clean (architecture file added outside repo originally, now tracked)
- Inspection date: 2026-11-23

## Environment
- OS: Windows (win32)
- Python: 3.12.1
- Rust/Cargo: 1.100.0
- FastRoute version: 0.1.14
- KiCad: not installed (no pcbnew, no kicad-cli)
- MCP SDK: 2.3.0
- Pydantic: 2.13.5

## Binary
- Path used: D:\FastrouteRS-MCP\target\debug\fastroute.exe
- Version: fastroute 0.1.14
- Help fingerprint: captured at runtime via fastroute_binary.help_fingerprint

## Integration points verified
- CLI flags use `=` form: --max-time=, --report=, --live=, --multi-start=, --initial-session=, --router.*=
- -de / -do accept space-separated values.
- --report writes JSON with keys: fastroute, input, time_s, stats, unrouted, clearance_violations, diagnosis (with --diagnose).
- Process emits stderr progress; stdout empty.
- SSE live stream on 127.0.0.1 (best-effort).

## Caveats
- KiCad not installed; board inspection/import/DRC adapters return unavailable.
- FastRoute binary built in debug mode; release build recommended for production.
- No pause/resume support; resume creates new job with --initial-session.
