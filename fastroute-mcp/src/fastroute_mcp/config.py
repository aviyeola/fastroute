from __future__ import annotations
import os
from pathlib import Path
from pydantic_settings import BaseSettings
from .paths import default_workspace_root


class Settings(BaseSettings):
    # Workspace
    workspace_root: Path = default_workspace_root()
    allowed_input_roots: list[Path] = [Path.home(), Path.cwd()]
    allowed_output_roots: list[Path] = [Path.home(), Path.cwd()]
    # FastRoute binary (trusted discovery)
    fastroute_bin: str | None = None
    # KiCad
    kicad_python: str | None = None
    kicad_cli: str | None = None
    # Limits
    max_board_bytes: int = 200 * 1024 * 1024
    max_concurrent_jobs: int = 2
    default_grace_seconds: int = 60
    # Logging
    log_level: str = "INFO"

    class Config:
        env_prefix = "FASTROUTE_MCP_"
        env_file = ".env"


def load_settings() -> Settings:
    s = Settings()
    # env overrides
    if os.environ.get("FASTROUTE_BIN"):
        s.fastroute_bin = os.environ["FASTROUTE_BIN"]
    if os.environ.get("KICAD_PYTHON"):
        s.kicad_python = os.environ["KICAD_PYTHON"]
    if os.environ.get("KICAD_CLI"):
        s.kicad_cli = os.environ["KICAD_CLI"]
    s.workspace_root = Path(s.workspace_root).expanduser()
    s.workspace_root.mkdir(parents=True, exist_ok=True)
    return s
