from __future__ import annotations
import os, shutil, subprocess, sys
from pathlib import Path
from typing import Optional


def discover_kicad_python(configured: str | None = None) -> Optional[str]:
    if configured and Path(configured).exists():
        return configured
    env = os.environ.get("KICAD_PYTHON")
    if env and Path(env).exists():
        return env
    # common names
    for name in ("python3", "python"):
        # KiCad's python often named python3 with pcbnew; try which
        found = shutil.which(name)
        if found:
            # test pcbnew import
            try:
                r = subprocess.run([found, "-c", "import pcbnew; print('ok')"],
                                   capture_output=True, text=True, timeout=10)
                if r.returncode == 0 and "ok" in r.stdout:
                    return found
            except Exception:
                pass
    # KiCad bundled python paths (Windows)
    for cand in (
        r"C:\Program Files\KiCad\9.0\bin\python.exe",
        r"C:\Program Files\KiCad\8.0\bin\python.exe",
        r"C:\Program Files\KiCad\7.0\bin\python.exe",
    ):
        if Path(cand).exists():
            try:
                r = subprocess.run([cand, "-c", "import pcbnew; print('ok')"],
                                   capture_output=True, text=True, timeout=10)
                if r.returncode == 0 and "ok" in r.stdout:
                    return cand
            except Exception:
                pass
    return None


def discover_kicad_cli(configured: str | None = None) -> Optional[str]:
    if configured and Path(configured).exists():
        return configured
    env = os.environ.get("KICAD_CLI")
    if env and Path(env).exists():
        return env
    found = shutil.which("kicad-cli")
    if found:
        return found
    for cand in (
        r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
        r"C:\Program Files\KiCad\8.0\bin\kicad-cli.exe",
        r"C:\Program Files\KiCad\7.0\bin\kicad-cli.exe",
    ):
        if Path(cand).exists():
            return cand
    return None


def kicad_version(kicad_python: str | None) -> str | None:
    if not kicad_python:
        return None
    try:
        r = subprocess.run([kicad_python, "-c", "import pcbnew; print(pcbnew.Version())"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def export_dsn(kicad_python: str, board_path: Path, dsn_path: Path) -> tuple[bool, str]:
    """Use core.py export via a small script. Returns (ok, message)."""
    core = None
    # find core.py in repo
    for cand in (
        Path.cwd() / "integrations" / "kicad" / "plugins" / "core.py",
        Path(__file__).resolve().parents[3] / "integrations" / "kicad" / "plugins" / "core.py",
    ):
        if cand.exists():
            core = cand
            break
    if core is None:
        return False, "core.py not found"
    script = f'''
import sys
sys.path.insert(0, {str(core.parent)!r})
import pcbnew
from core import export_dsn
board = pcbnew.LoadBoard({str(board_path)!r})
ok = export_dsn(board, {str(dsn_path)!r})
print("OK" if ok else "FAIL")
'''
    try:
        r = subprocess.run([kicad_python, "-c", script], capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and "OK" in r.stdout:
            return True, "exported"
        return False, (r.stderr or r.stdout).strip()
    except Exception as e:
        return False, str(e)


def import_ses_to_board(kicad_python: str, board_path: Path, ses_path: Path, out_board_path: Path, refill_zones: bool = True) -> tuple[bool, str]:
    core = None
    for cand in (
        Path.cwd() / "integrations" / "kicad" / "plugins" / "core.py",
        Path(__file__).resolve().parents[3] / "integrations" / "kicad" / "plugins" / "core.py",
    ):
        if cand.exists():
            core = cand
            break
    if core is None:
        return False, "core.py not found"
    script = f'''
import sys
sys.path.insert(0, {str(core.parent)!r})
import pcbnew
from core import import_ses
board = pcbnew.LoadBoard({str(board_path)!r})
ok = import_ses(board, {str(ses_path)!r})
if ok:
    if {refill_zones!r}:
        try:
            pcbnew.REFRESH()
        except Exception:
            pass
    pcbnew.SaveBoard({str(out_board_path)!r}, board)
print("OK" if ok else "FAIL")
'''
    try:
        r = subprocess.run([kicad_python, "-c", script], capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and "OK" in r.stdout:
            return True, "imported"
        return False, (r.stderr or r.stdout).strip()
    except Exception as e:
        return False, str(e)


def run_kicad_drc(kicad_cli: str, board_path: Path, report_path: Path) -> tuple[bool, str]:
    # Try version-compatible invocation
    try:
        r = subprocess.run([kicad_cli, "pcb", "drc", "--help"], capture_output=True, text=True, timeout=30)
        help_text = (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return False, str(e)
    # Common KiCad 8/9 syntax
    cmd = [kicad_cli, "pcb", "drc", "--output", str(report_path), str(board_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode == 0 or report_path.exists():
            return True, (r.stdout or "") + (r.stderr or "")
        return False, (r.stderr or r.stdout).strip()
    except Exception as e:
        return False, str(e)
