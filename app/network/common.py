"""
common.py — Utilidades de bajo nivel compartidas por los backends de red.

Está separado de arp.py para que los backends de plataforma puedan usarlo sin
importaciones circulares.
"""

import re
import subprocess
import sys
from typing import NamedTuple

IS_WINDOWS = sys.platform.startswith("win")


class Iface(NamedTuple):
    """Una interfaz de red IPv4 de esta máquina."""

    name: str          # 'wlo1' en Linux, 'Wi-Fi' en Windows
    ip: str            # '192.168.1.50'
    cidr: str          # '192.168.1.0/24'
    mac: str | None    # MAC propia de la interfaz, si se pudo averiguar

# En Windows cada subproceso abre una ventana de consola. Durante un barrido se
# lanzan cientos de pings: sin esto la pantalla se llena de parpadeos.
_FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}


def run(cmd: list[str], timeout: float = 5.0) -> str:
    """
    stdout del comando, o "" si falla, no existe o agota el tiempo.

    Se decodifica a mano en lugar de usar text=True: en Windows la salida de
    `arp -a` viene en la codificación OEM (cp850 y similares) y decodificarla
    como UTF-8 estricto reventaría. Los datos que interesan (IP y MAC) son
    ASCII, así que basta con ignorar los errores de los rótulos traducidos.
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, **_FLAGS)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.decode("utf-8", "replace")


def run_ok(cmd: list[str], timeout: float = 5.0) -> bool:
    """True si el comando termina con código 0."""
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, **_FLAGS)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


# ── Direcciones MAC ───────────────────────────────────────────────────────────

MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")

# Ni sin resolver, ni difusión: no identifican a ningún dispositivo.
_INCOMPLETE = {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}


def normalize_mac(mac: str | None) -> str | None:
    """
    Normaliza a 'aa:bb:cc:dd:ee:ff'. Devuelve None si no es una MAC válida.

    Acepta los formatos de todas las procedencias: 'aa:bb:...' (Linux),
    'AA-BB-...' (arp -a y PowerShell en Windows), 'aabb.ccdd.eeff' (Cisco) y
    'AABBCCDDEEFF'.
    """
    if not mac:
        return None
    value = mac.strip().lower().replace("-", ":").replace(".", ":")
    if ":" not in value and len(value) == 12:
        value = ":".join(value[i:i + 2] for i in range(0, 12, 2))
    if not MAC_RE.match(value) or value in _INCOMPLETE:
        return None
    return value
