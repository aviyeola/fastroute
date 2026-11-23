from __future__ import annotations
from typing import Any

PROFILES: dict[str, dict[str, Any]] = {
    "quick": {
        "description": "Fast feasibility check; router-only, modest time.",
        "max_time_seconds": 120,
        "max_passes": 1,
        "multi_start": 1,
        "optimizer_enabled": False,
        "parity": False,
    },
    "balanced": {
        "description": "Default FastRoute improvements, bounded time, normal optimizer.",
        "max_time_seconds": 600,
        "max_passes": 0,
        "multi_start": 4,
        "optimizer_enabled": True,
        "parity": False,
    },
    "quality": {
        "description": "Better optimization; longer budget, optimizer enabled, bounded multi-start.",
        "max_time_seconds": 1800,
        "max_passes": 0,
        "multi_start": 4,
        "optimizer_enabled": True,
        "parity": False,
    },
    "dense_board": {
        "description": "Congested designs; longer budget and controlled multi-start; no rule relaxation.",
        "max_time_seconds": 1800,
        "max_passes": 0,
        "multi_start": 6,
        "optimizer_enabled": True,
        "parity": False,
    },
    "parity": {
        "description": "Reproducibility against Freerouting; deterministic compatibility mode.",
        "max_time_seconds": 600,
        "max_passes": 0,
        "multi_start": 1,
        "optimizer_enabled": True,
        "parity": True,
    },
}

ALLOWED_ROUTER_KEYS = {
    "autorouter.max_passes",
    "autorouter.min_passes",
    "autorouter.ignore_net_classes",
    "autorouter.max_threads",
    "optimizer.enabled",
    "optimizer.max_threads",
    "optimizer.optimization_improvement_threshold",
    "fanout.enabled",
    "scoring.via_costs",
    "scoring.plane_via_costs",
}

# Maps incoming extra_options keys to validated CLI settings.
# We only allow a small typed set at top-level.
TOP_LEVEL_KEYS = {
    "max_time_seconds", "max_passes", "threads", "multi_start",
    "diagnose", "live_monitoring", "existing_tracks", "optimizer_enabled",
    "parity", "extra_router",  # extra_router is dict of router.* allowlisted
}


def validate_config(profile: str, overrides: dict | None = None) -> tuple[dict, list[str], list[str]]:
    """Return (effective_config, rejected, warnings)."""
    overrides = overrides or {}
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile: {profile}")
    cfg = dict(PROFILES[profile])
    # overrides top-level
    rejected = []
    warnings = []
    for k, v in overrides.items():
        if k in ("max_time_seconds", "max_passes", "threads", "multi_start", "diagnose",
                 "live_monitoring", "existing_tracks", "optimizer_enabled", "parity",
                 "profile", "description"):
            if k not in ("profile", "description"):
                cfg[k] = v
        elif k == "router_overrides":
            rr = cfg.setdefault("router_overrides", {})
            for rk, rv in (v or {}).items():
                if rk in ALLOWED_ROUTER_KEYS:
                    rr[rk] = rv
                else:
                    rejected.append(f"router key not allowlisted: {rk}")
        elif k == "extra_router":
            # dict of router path -> value, allowlisted
            rr = cfg.setdefault("router_overrides", {})
            for rk, rv in (v or {}).items():
                if rk in ALLOWED_ROUTER_KEYS:
                    rr[rk] = rv
                else:
                    rejected.append(f"router key not allowlisted: {rk}")
        elif k == "extra_options":
            # legacy
            for rk, rv in (v or {}).items():
                if rk in ALLOWED_ROUTER_KEYS:
                    cfg.setdefault("router_overrides", {})[rk] = rv
                else:
                    rejected.append(f"router key not allowlisted: {rk}")
        else:
            rejected.append(f"unknown config key: {k}")
    # normalize
    cfg.setdefault("router_overrides", {})
    cfg["profile"] = profile
    # existing_tracks validation
    et = cfg.get("existing_tracks", "router_default")
    if et not in ("router_default", "keep_existing", "clear_unlocked"):
        rejected.append(f"invalid existing_tracks: {et}")
        cfg["existing_tracks"] = "router_default"
    if cfg.get("threads") is not None and (not isinstance(cfg["threads"], int) or cfg["threads"] < 1):
        rejected.append("threads must be positive int")
        cfg["threads"] = None
    if cfg.get("multi_start", 1) < 1:
        rejected.append("multi_start must be >=1")
        cfg["multi_start"] = 1
    if cfg.get("max_time_seconds", 0) < 0:
        rejected.append("max_time_seconds must be >=0")
        cfg["max_time_seconds"] = max(0, cfg.get("max_time_seconds", 0))
    return cfg, rejected, warnings
