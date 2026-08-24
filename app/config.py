"""
config.py — Carga de config.toml con valores por defecto.

Se lee una sola vez al arrancar. Todo lo que puede cambiar en caliente
(submissions abiertas/cerradas, problemas activos) vive en la base de datos,
no aquí.
"""

import os
import tomllib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("HACK_CONFIG", BASE_DIR / "config.toml"))

DEFAULTS: dict = {
    "event": {"title": "Hackathon", "submissions_open": True},
    "server": {"host": "0.0.0.0", "port": 8000, "max_upload_mb": 2},
    "admin": {
        "password": "cambiame",
        "require_admin_mac": True,
        "session_hours": 12,
    },
    "network": {
        "enabled": True,
        "interface": "auto",
        "subnet": "auto",
        "scan_interval_secs": 20,
        "miss_threshold": 3,
        "ping_timeout_secs": 1,
        "max_parallel_pings": 64,
        "prefer_arp_scan": True,
        "dev_mac": "",
    },
    "execution": {
        "max_concurrent": 3,
        "build_timeout_secs": 300,
        "keep_images": True,
    },
    "paths": {"db": "data/hackathon.db", "submissions": "data/submissions"},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _load() -> dict:
    if not CONFIG_PATH.exists():
        return DEFAULTS
    with open(CONFIG_PATH, "rb") as f:
        return _merge(DEFAULTS, tomllib.load(f))


_CFG = _load()


def _resolve(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else BASE_DIR / path


# ── Evento ────────────────────────────────────────────────────────────────────
EVENT_TITLE: str = _CFG["event"]["title"]
SUBMISSIONS_OPEN_DEFAULT: bool = bool(_CFG["event"]["submissions_open"])

# ── Servidor ──────────────────────────────────────────────────────────────────
HOST: str = _CFG["server"]["host"]
PORT: int = int(_CFG["server"]["port"])
MAX_UPLOAD_BYTES: int = int(_CFG["server"]["max_upload_mb"] * 1024 * 1024)

# ── Admin ─────────────────────────────────────────────────────────────────────
ADMIN_PASSWORD: str = _CFG["admin"]["password"]
ADMIN_REQUIRE_MAC: bool = bool(_CFG["admin"]["require_admin_mac"])
ADMIN_SESSION_HOURS: int = int(_CFG["admin"]["session_hours"])

# ── Red ───────────────────────────────────────────────────────────────────────
NET_ENABLED: bool = bool(_CFG["network"]["enabled"])
NET_INTERFACE: str = _CFG["network"]["interface"]
NET_SUBNET: str = _CFG["network"]["subnet"]
NET_SCAN_INTERVAL: int = int(_CFG["network"]["scan_interval_secs"])
NET_MISS_THRESHOLD: int = int(_CFG["network"]["miss_threshold"])
NET_PING_TIMEOUT: int = int(_CFG["network"]["ping_timeout_secs"])
NET_MAX_PARALLEL: int = int(_CFG["network"]["max_parallel_pings"])
NET_PREFER_ARP_SCAN: bool = bool(_CFG["network"]["prefer_arp_scan"])
NET_DEV_MAC: str = (_CFG["network"]["dev_mac"] or "").strip().lower()

# ── Ejecución ─────────────────────────────────────────────────────────────────
MAX_CONCURRENT: int = int(_CFG["execution"]["max_concurrent"])
BUILD_TIMEOUT_SECS: int = int(_CFG["execution"]["build_timeout_secs"])
KEEP_IMAGES: bool = bool(_CFG["execution"]["keep_images"])

# ── Rutas ─────────────────────────────────────────────────────────────────────
DB_PATH: Path = _resolve(_CFG["paths"]["db"])
SUBMISSIONS_DIR: Path = _resolve(_CFG["paths"]["submissions"])
PROBLEMS_DIR: Path = BASE_DIR / "problems"
DEVICES_FILE: Path = BASE_DIR / "devices.toml"
