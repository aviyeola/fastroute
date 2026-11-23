from __future__ import annotations
import asyncio, hashlib, json, os, signal, subprocess, sys, threading, time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional
from ..errors import FastRouteError, envelope_ok
from ..config import Settings
from ..models.job import Job, JobState
from ..models.event import NormalizedEvent
from ..paths import normalize_path
from ..security import validate_path
from .persistence import Persistence
from .profile_manager import validate_config
from .fastroute_binary import find_fastroute, version_string, help_fingerprint
from .kicad_adapter import discover_kicad_python, discover_kicad_cli, kicad_version, export_dsn, import_ses_to_board


import collections


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Persistence(settings.workspace_root.parent / "fastroute.db")
        self.binary = find_fastroute(settings.fastroute_bin)
        self.kicad_python = discover_kicad_python(settings.kicad_python)
        self.kicad_cli = discover_kicad_cli(settings.kicad_cli)
        self.kicad_ver = kicad_version(self.kicad_python)
        self.fastroute_ver = version_string(self.binary) if self.binary else None
        self.jobs: dict[str, Job] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.stdout_buffers: dict[str, collections.deque] = {}
        self.stderr_buffers: dict[str, collections.deque] = {}
        self.log_files: dict[str, tuple[Any, Any]] = {}
        self.event_health: dict[str, str] = {}
        self.latest_frame: dict[str, dict] = {}
        self._load_existing()

    def _load_existing(self):
        # reconcile persisted jobs
        for j in self.db.list_jobs(limit=10000):
            self.jobs[j.job_id] = j
            # mark orphaned running as interrupted
            if j.state in (JobState.running, JobState.stop_requested, JobState.finalizing, JobState.preparing, JobState.queued):
                j.state = JobState.interrupted
                self.db.save_job(j)

    def allowed_roots(self) -> list[Path]:
        return [normalize_path(p) for p in (self.settings.allowed_input_roots + self.settings.allowed_output_roots)]

    def _workspace(self, job_id: str) -> Path:
        return self.settings.workspace_root / job_id

    def _new_job_id(self) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        # ensure uniqueness
        base = f"fr_{ts}"
        i = 1
        jid = base
        while jid in self.jobs or self.db.get_job(jid):
            jid = f"{base}_{i}"
            i += 1
        return jid

    async def start_routing(self, board_path: str, profile: str = "balanced",
                            max_time_seconds: int | None = None,
                            max_passes: int | None = None,
                            threads: int | None = None,
                            multi_start: int | None = None,
                            diagnose: bool = False,
                            live_monitoring: bool = True,
                            existing_tracks: str = "router_default",
                            extra_options: dict | None = None,
                            parent_job_id: str | None = None,
                            initial_session_path: str | None = None) -> Job:
        # concurrency limit
        import json, hashlib, shutil
        active = [j for j in self.jobs.values() if j.state in (JobState.running, JobState.preparing, JobState.queued, JobState.stop_requested)]
        if len(active) >= self.settings.max_concurrent_jobs:
            raise FastRouteError("JOB_LIMIT_REACHED", "Concurrency limit reached", True)
        if self.binary is None:
            raise FastRouteError("FASTROUTE_NOT_FOUND", "FastRoute binary not found", True)

        roots = self.allowed_roots()
        bp = validate_path(board_path, roots, must_exist=True)
        if bp.stat().st_size > self.settings.max_board_bytes:
            raise FastRouteError("BOARD_INVALID", "Board file too large", False)

        # config
        overrides = {
            "existing_tracks": existing_tracks,
        }
        if max_time_seconds is not None:
            overrides["max_time_seconds"] = max_time_seconds
        if max_passes is not None:
            overrides["max_passes"] = max_passes
        if threads is not None:
            overrides["threads"] = threads
        if multi_start is not None:
            overrides["multi_start"] = multi_start
        overrides["diagnose"] = diagnose
        overrides["live_monitoring"] = live_monitoring
        if extra_options:
            overrides["extra_options"] = extra_options
        # map extra_options router keys
        cfg, rejected, warnings = validate_config(profile, overrides)
        if rejected:
            raise FastRouteError("CONFIG_REJECTED", "; ".join(rejected), False, {"rejected": rejected})
        if cfg.get("diagnose") is None:
            cfg["diagnose"] = diagnose

        job_id = self._new_job_id()
        ws = self._workspace(job_id)
        ws.mkdir(parents=True, exist_ok=True)
        for sub in ("input", "config", "logs", "output", "tmp"):
            (ws / sub).mkdir(exist_ok=True)

        # snapshot board
        snap = ws / "input" / ("source" + bp.suffix)
        import shutil
        shutil.copy2(bp, snap)
        board_sha = sha256_file(snap)

        # initial session
        init_ses = None
        init_ses_hash = None
        if initial_session_path:
            ip = validate_path(initial_session_path, roots, must_exist=True)
            init_ses = ws / "input" / "initial.ses"
            shutil.copy2(ip, init_ses)
            init_ses_hash = sha256_file(init_ses)

        # DSN export
        dsn_path = ws / "input" / "board.dsn"
        if bp.suffix.lower() == ".dsn":
            shutil.copy2(bp, dsn_path)
        elif bp.suffix.lower() == ".kicad_pcb":
            if not self.kicad_python:
                raise FastRouteError("KICAD_NOT_FOUND", "KiCad Python not available for DSN export", True)
            ok, msg = export_dsn(self.kicad_python, snap, dsn_path)
            if not ok:
                raise FastRouteError("EXPORT_FAILED", f"DSN export failed: {msg}", True)
        else:
            raise FastRouteError("BOARD_INVALID", f"Unsupported board extension: {bp.suffix}", False)

        # config hash
        import hashlib
        cfg_json = json.dumps(cfg, sort_keys=True)
        config_hash = hashlib.sha256(cfg_json.encode()).hexdigest()

        # effective config persisted
        job = Job(
            job_id=job_id,
            state=JobState.created,
            board_path=str(bp),
            board_sha256=board_sha,
            dsn_path=str(dsn_path),
            ses_path=str(ws / "output" / "best.ses"),
            report_path=str(ws / "output" / "report.json"),
            routed_board_path=str(ws / "output" / "routed.kicad_pcb"),
            drc_report_path=str(ws / "output" / "drc_report.json"),
            config_hash=config_hash,
            fastroute_version=self.fastroute_ver,
            fastroute_binary=str(self.binary),
            kicad_version=self.kicad_ver,
            parent_job_id=parent_job_id,
            attempt_index=1,
            profile=profile,
            effective_config=cfg,
            workspace=str(ws),
            initial_session_path=str(init_ses) if init_ses else None,
            initial_session_hash=init_ses_hash,
        )
        # attempt_index if parent
        if parent_job_id:
            pj = self.jobs.get(parent_job_id) or self.db.get_job(parent_job_id)
            if pj:
                job.attempt_index = (pj.attempt_index or 1) + 1

        # write config files
        (ws / "config" / "effective_config.json").write_text(json.dumps(cfg, indent=2))
        argv = self._build_argv(job, cfg, dsn_path, ws)
        job.argv = argv
        (ws / "config" / "args.txt").write_text(" ".join(argv))

        self.jobs[job_id] = job
        self.db.save_job(job)
        # manifest
        import json
        (ws / "manifest.json").write_text(json.dumps(job.model_dump(), indent=2, default=str))
        self.stdout_buffers[job_id] = collections.deque(maxlen=10000)
        self.stderr_buffers[job_id] = collections.deque(maxlen=10000)

        # launch async
        task = asyncio.create_task(self._run_job(job))
        self.tasks[job_id] = task
        return job

    def _build_argv(self, job: Job, cfg: dict, dsn_path: Path, ws: Path) -> list[str]:
        binary = str(self.binary)
        argv = [binary, "-de", str(dsn_path), "-do", str(ws / "output" / "best.ses")]
        if cfg.get("max_time_seconds"):
            argv += [f"--max-time={cfg['max_time_seconds']}"]
        if cfg.get("max_passes"):
            argv += [f"--router.autorouter.max_passes={cfg['max_passes']}"]
        if cfg.get("multi_start"):
            argv += [f"--multi-start={cfg['multi_start']}"]
        if cfg.get("parity"):
            argv += ["--parity"]
        if cfg.get("diagnose"):
            argv += ["--diagnose"]
        # report
        argv += [f"--report={ws / 'output' / 'report.json'}"]
        # live
        if cfg.get("live_monitoring", True):
            argv += ["--live=0"]
        if cfg.get("optimizer_enabled") is False:
            argv += ["--router.optimizer.enabled=false"]
        threads = cfg.get("threads")
        if threads:
            argv += [f"--router.autorouter.max_threads={threads}"]
        # router overrides
        for k, v in (cfg.get("router_overrides") or {}).items():
            argv.append(f"--router.{k}={v}")
        # initial session
        if job.initial_session_path:
            argv += [f"--initial-session={job.initial_session_path}"]
        # existing_tracks handling is KiCad-side; FastRoute doesn't know. We store in config only.
        return argv

    async def _run_job(self, job: Job):
        job.state = JobState.preparing
        self.db.save_job(job)
        ws = Path(job.workspace)
        # allocate live port if requested
        live_requested = "--live=0" in job.argv
        port = None
        if live_requested:
            port = await self._find_free_port()
            job.argv = [a.replace("--live=0", f"--live={port}") if a.startswith("--live=") else a for a in job.argv]
            job.live_port = port
        # ensure report path
        stdout_path = ws / "logs" / "stdout.log"
        stderr_path = ws / "logs" / "stderr.log"
        f_out = open(stdout_path, "wb")
        f_err = open(stderr_path, "wb")
        self.log_files[job.job_id] = (f_out, f_err)

        job.state = JobState.running
        job.started_at = datetime.now(timezone.utc).isoformat()
        self.db.save_job(job)

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            proc = await asyncio.create_subprocess_exec(
                *job.argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        except Exception as e:
            job.state = JobState.failed
            job.error_code = "PROCESS_START_FAILED"
            job.error_message = str(e)
            job.finished_at = datetime.now(timezone.utc).isoformat()
            self.db.save_job(job)
            f_out.close(); f_err.close()
            return

        self.processes[job.job_id] = proc
        job.pid = proc.pid
        self.db.save_job(job)

        # readers
        async def read_stream(stream, buf, f):
            while True:
                line = await stream.readline()
                if not line:
                    break
                buf.append(line.decode(errors="replace").rstrip("\n"))
                f.write(line)
                f.flush()

        t1 = asyncio.create_task(read_stream(proc.stdout, self.stdout_buffers[job.job_id], f_out))
        t2 = asyncio.create_task(read_stream(proc.stderr, self.stderr_buffers[job.job_id], f_err))

        # SSE collector
        sse_task = None
        if port:
            sse_task = asyncio.create_task(self._sse_collect(job, port))

        # wait
        try:
            await proc.wait()
        except Exception:
            pass
        t1.cancel(); t2.cancel()
        if sse_task:
            sse_task.cancel()

        job.exit_code = proc.returncode
        f_out.close(); f_err.close()

        # finalize
        job.state = JobState.finalizing
        self.db.save_job(job)
        await self._finalize_job(job)
        self.db.save_job(job)

    async def _find_free_port(self) -> int:
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    async def _sse_collect(self, job: Job, port: int):
        url = f"http://127.0.0.1:{port}/events"
        self.event_health[job.job_id] = "connecting"
        # wait for server ready
        await asyncio.sleep(1)
        try:
            async with __import__("httpx").AsyncClient(timeout=None) as client:
                async with client.stream("GET", url) as resp:
                    self.event_health[job.job_id] = "connected"
                    buf = ""
                    event_type = None
                    async for chunk in resp.aiter_text():
                        for line in chunk.splitlines(keepends=True):
                            line = line.rstrip("\n")
                            if line.startswith("event:"):
                                event_type = line[6:].strip()
                            elif line.startswith("data:"):
                                data = line[5:].strip()
                                buf += data
                            elif line == "":
                                if event_type or buf:
                                    await self._handle_sse_event(job, event_type or "message", buf)
                                    buf = ""
                                    event_type = None
        except Exception as e:
            self.event_health[job.job_id] = "degraded"
            # persist service event
            ev = NormalizedEvent(event_id=0, job_id=job.job_id, type="event_stream.degraded",
                                 source="service", payload={"error": str(e)})
            eid = self.db.add_event(ev)
            ev.event_id = eid

    async def _handle_sse_event(self, job: Job, event_type: str, data: str):
        import json
        try:
            payload = json.loads(data) if data else {}
        except Exception:
            payload = {"raw": data}
        # map
        mapping = {
            "board": "board.loaded",
            "stage": "routing.stage_changed",
            "tick": "routing.progress",
            "pass": None,  # determine below
            "log": "process.log",
            "done": "routing.live_stream_done",
            "frame": None,
        }
        if event_type == "pass":
            kind = payload.get("kind") if isinstance(payload, dict) else None
            ntype = "routing.pass_completed" if kind != "optimizer" else "optimizer.pass_completed"
        elif event_type == "frame":
            # store latest frame only
            self.latest_frame[job.job_id] = payload
            return
        else:
            ntype = mapping.get(event_type)
            if ntype is None:
                ntype = f"process.{event_type}"
        if ntype is None:
            return
        ev = NormalizedEvent(event_id=0, job_id=job.job_id, type=ntype,
                             source="fastroute_sse", payload=payload if isinstance(payload, dict) else {"raw": payload})
        eid = self.db.add_event(ev)
        ev.event_id = eid
        # update job latest metrics from pass/tick
        if ntype in ("routing.pass_completed", "optimizer.pass_completed") and isinstance(payload, dict):
            # store in job? not persisted metric, but status can read last event
            pass

    async def _finalize_job(self, job: Job):
        ws = Path(job.workspace)
        report_path = Path(job.report_path)
        ses_path = Path(job.ses_path)
        # determine state
        if job.stop_requested_at:
            if ses_path.exists():
                job.state = JobState.stopped
            else:
                job.state = JobState.stopped
        else:
            if report_path.exists():
                job.state = JobState.completed
            elif job.exit_code == 0:
                job.state = JobState.completed
            elif ses_path.exists():
                job.state = JobState.completed_partial
            else:
                job.state = JobState.failed
                if job.error_code is None:
                    job.error_code = "REPORT_MISSING" if not report_path.exists() else None
        if job.exit_code not in (0, None) and not report_path.exists() and not ses_path.exists():
            job.state = JobState.failed
            if job.error_code is None:
                job.error_code = "PROCESS_FAILED"
                job.error_message = f"exit code {job.exit_code}"
        job.finished_at = datetime.now(timezone.utc).isoformat()
        # service event
        ev = NormalizedEvent(event_id=0, job_id=job.job_id, type="job.finalized",
                             source="service", payload={"state": job.state.value, "exit_code": job.exit_code})
        eid = self.db.add_event(ev)
        ev.event_id = eid
        import json
        (Path(job.workspace) / "manifest.json").write_text(json.dumps(job.model_dump(), indent=2, default=str))

    async def stop_routing(self, job_id: str, force: bool = False, grace_seconds: int = 60) -> dict:
        job = self._get_job(job_id)
        if job.state not in (JobState.running, JobState.stop_requested, JobState.preparing):
            raise FastRouteError("JOB_STATE_INVALID", f"Cannot stop job in state {job.state}", False)
        proc = self.processes.get(job_id)
        if proc is None or proc.returncode is not None:
            # already exited
            return {"requested": False, "process_state": "exited", "checkpoint_exists": Path(job.ses_path).exists()}
        job.state = JobState.stop_requested
        job.stop_requested_at = datetime.now(timezone.utc).isoformat()
        self.db.save_job(job)
        ev = NormalizedEvent(event_id=0, job_id=job_id, type="job.stop_requested", source="service", payload={})
        eid = self.db.add_event(ev); ev.event_id = eid

        signaled = False
        try:
            if sys.platform == "win32":
                os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
            else:
                os.kill(proc.pid, signal.SIGINT)
            signaled = True
        except Exception as e:
            if force:
                try:
                    proc.kill()
                    signaled = True
                except Exception:
                    pass
            else:
                raise FastRouteError("GRACEFUL_STOP_TIMEOUT", f"Could not signal process: {e}", True)

        # wait grace
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        except asyncio.TimeoutError:
            if not force:
                job.state = JobState.stop_requested
                self.db.save_job(job)
                return {"requested": True, "signal_sent": signaled, "process_state": "running",
                        "checkpoint_exists": Path(job.ses_path).exists(),
                        "grace_timeout": True}
            else:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
        # update
        job.exit_code = proc.returncode
        await self._finalize_job(job)
        self.db.save_job(job)
        return {"requested": True, "signal_sent": signaled, "process_state": "exited",
                "checkpoint_exists": Path(job.ses_path).exists()}

    def _get_job(self, job_id: str) -> Job:
        job = self.jobs.get(job_id) or self.db.get_job(job_id)
        if not job:
            raise FastRouteError("JOB_NOT_FOUND", f"No job {job_id}", False)
        self.jobs[job_id] = job
        return job

    async def resume_routing(self, source_job_id: str, profile: str | None = None,
                             max_time_seconds: int | None = None, multi_start: int | None = None,
                             diagnose: bool | None = None, approved_board_hash: str | None = None) -> Job:
        src = self._get_job(source_job_id)
        if src.state not in (JobState.completed, JobState.completed_partial, JobState.stopped, JobState.interrupted, JobState.failed):
            raise FastRouteError("JOB_STATE_INVALID", f"Source job state {src.state} not resumable", False)
        ses = Path(src.ses_path) if src.ses_path else None
        if not ses or not ses.exists():
            raise FastRouteError("CHECKPOINT_MISSING", "No best SES checkpoint", False)
        # board hash check
        # find source board snapshot? use src.board_sha256
        if approved_board_hash and approved_board_hash != src.board_sha256:
            # allow new board hash only if approved (we don't verify new board here)
            pass
        # For resume we reuse same board_path (source original). 
        prof = profile or src.profile
        overrides = {}
        if max_time_seconds is not None:
            overrides["max_time_seconds"] = max_time_seconds
        if multi_start is not None:
            overrides["multi_start"] = multi_start
        if diagnose is not None:
            overrides["diagnose"] = diagnose
        # Build config from src effective config then override
        base_cfg = dict(src.effective_config)
        # remove profile key
        base_cfg.pop("profile", None)
        # apply overrides
        for k, v in overrides.items():
            base_cfg[k] = v
        # validate
        cfg, rejected, warnings = validate_config(prof, base_cfg)
        if rejected:
            raise FastRouteError("CONFIG_REJECTED", "; ".join(rejected), False)
        # We'll call start_routing with extra handling for initial session.
        # Use src board_path
        job = await self.start_routing(
            board_path=src.board_path,
            profile=prof,
            max_time_seconds=cfg.get("max_time_seconds"),
            max_passes=cfg.get("max_passes"),
            threads=cfg.get("threads"),
            multi_start=cfg.get("multi_start"),
            diagnose=cfg.get("diagnose", False),
            live_monitoring=cfg.get("live_monitoring", True),
            existing_tracks=cfg.get("existing_tracks", "router_default"),
            extra_options=cfg.get("router_overrides", {}),
            parent_job_id=source_job_id,
            initial_session_path=str(ses),
        )
        # override effective config with resumed cfg (start_routing recomputes) - ok
        return job

    def get_status(self, job_id: str) -> dict:
        job = self._get_job(job_id)
        # latest pass metrics from events
        events = self.db.get_events(job_id, after_event_id=0, limit=1000)
        last_pass = None
        last_stage = None
        latest_unrouted = None
        latest_violations = None
        last_ts = None
        for e in events:
            if e["type"] in ("routing.pass_completed", "optimizer.pass_completed"):
                last_pass = e
                p = e.get("payload", {})
                if isinstance(p, dict):
                    latest_unrouted = p.get("unrouted", latest_unrouted)
                    latest_violations = p.get("violations", latest_violations)
            if e["type"] == "routing.stage_changed":
                last_stage = e.get("payload", {}).get("stage") if isinstance(e.get("payload"), dict) else None
            last_ts = e.get("received_at")
        elapsed = None
        if job.started_at:
            try:
                start = datetime.fromisoformat(job.started_at.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc) if job.finished_at is None else datetime.fromisoformat(job.finished_at.replace("Z", "+00:00"))
                elapsed = (now - start).total_seconds()
            except Exception:
                pass
        return {
            "job_id": job.job_id,
            "state": job.state.value,
            "elapsed_seconds": elapsed,
            "current_stage": last_stage,
            "latest_pass": last_pass.get("payload") if last_pass else None,
            "latest_unrouted": latest_unrouted,
            "latest_violations": latest_violations,
            "best_ses": {"path": job.ses_path, "exists": Path(job.ses_path).exists()} if job.ses_path else None,
            "pid": job.pid,
            "last_event_at": last_ts,
            "event_stream_health": self.event_health.get(job.job_id, "unknown"),
            "report_available": Path(job.report_path).exists() if job.report_path else False,
            "next_recommended_action": self._recommend(job),
        }

    def _recommend(self, job: Job) -> str:
        if job.state in (JobState.completed, JobState.completed_partial, JobState.stopped):
            return "read report/diagnostics; consider resume_routing for another attempt"
        if job.state == JobState.running:
            return "monitor via get_routing_status/get_routing_events"
        if job.state == JobState.failed:
            return "inspect logs; fix config/environment"
        return "none"

    def get_logs(self, job_id: str, stream: str = "combined", after_line: int = 0, limit: int = 500) -> dict:
        job = self._get_job(job_id)
        if stream == "stdout":
            lines = list(self.stdout_buffers.get(job_id, []))
        elif stream == "stderr":
            lines = list(self.stderr_buffers.get(job_id, []))
        else:
            # combined: read files
            ws = Path(job.workspace)
            out_lines = []
            try:
                out_lines += (ws / "logs" / "stdout.log").read_text(errors="replace").splitlines()
            except Exception:
                pass
            try:
                out_lines += (ws / "logs" / "stderr.log").read_text(errors="replace").splitlines()
            except Exception:
                pass
            lines = out_lines
        # apply after_line/limit
        total = len(lines)
        start = min(after_line, total)
        chunk = lines[start:start+limit]
        return {"lines": chunk, "next_cursor": start + len(chunk), "truncated": start + len(chunk) < total}

    def get_events(self, job_id: str, after_event_id: int = 0, limit: int = 500) -> dict:
        self._get_job(job_id)
        rows = self.db.get_events(job_id, after_event_id, limit)
        events = []
        for r in rows:
            events.append({
                "event_id": r["event_id"],
                "job_id": r["job_id"],
                "type": r["type"],
                "source": r["source"],
                "occurred_at": r["occurred_at"],
                "received_at": r["received_at"],
                "payload": r["payload"],
            })
        next_id = rows[-1]["event_id"] if rows else after_event_id
        health = self.event_health.get(job_id, "unknown")
        return {"events": events, "next_cursor": next_id, "stream_health": health}

    def list_jobs(self, board_sha256: str | None = None, states: list[str] | None = None, limit: int = 50, cursor: int = 0) -> dict:
        jobs = self.db.list_jobs(board_sha256, states, limit, cursor)
        total = self.db.count_jobs()
        return {"jobs": [self._job_summary(j) for j in jobs], "total": total, "next_cursor": cursor + len(jobs) if cursor + len(jobs) < total else None}

    def _job_summary(self, job: Job) -> dict:
        return {
            "job_id": job.job_id,
            "state": job.state.value,
            "board_sha256": job.board_sha256,
            "profile": job.profile,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "parent_job_id": job.parent_job_id,
            "attempt_index": job.attempt_index,
        }
