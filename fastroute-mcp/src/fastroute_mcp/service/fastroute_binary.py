from __future__ import annotations
import os, shutil, subprocess
from pathlib import Path
from .kicad_adapter import discover_kicad_python, discover_kicad_cli, kicad_version


def find_fastroute(configured: str | None = None) -> Path | None:
    if configured:
        p = Path(configured)
        if p.is_file():
            return p.resolve()
    env = os.environ.get("FASTROUTE_BIN")
    if env:
        p = Path(env)
        if p.is_file():
            return p.resolve()
    # repo target/debug
    for cand in (
        Path.cwd() / "target" / "debug" / "fastroute.exe",
        Path.cwd() / "target" / "release" / "fastroute.exe",
        Path.cwd() / "target" / "debug" / "fastroute",
        Path.cwd() / "target" / "release" / "fastroute",
    ):
        if cand.is_file():
            return cand.resolve()
    # walk up
    here = Path(__file__).resolve()
    for parent in here.parents:
        for rel in ("target/debug/fastroute.exe", "target/release/fastroute.exe",
                    "target/debug/fastroute", "target/release/fastroute"):
            cand = parent / rel
            if cand.is_file():
                return cand.resolve()
    found = shutil.which("fastroute") or shutil.which("fastroute.exe")
    if found:
        return Path(found).resolve()
    return None


def version_string(binary: Path) -> str:
    try:
        r = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=10)
        return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else "unknown"
    except Exception:
        return "unknown"


def help_fingerprint(binary: Path) -> str:
    try:
        r = subprocess.run([str(binary), "--help"], capture_output=True, text=True, timeout=10)
        out = (r.stdout or "") + (r.stderr or "")
        import hashlib
        return hashlib.sha256(out.encode()).hexdigest()
    except Exception:
        return ""
