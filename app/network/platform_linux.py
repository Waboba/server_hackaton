"""
platform_linux.py — Backend de red para Linux.

Usa iproute2 (`ip neigh`, `ip addr`), con /proc como respaldo para sistemas que
no lo tengan. Nada de esto necesita privilegios de root.

Implementa el contrato que espera arp.py:
    neigh_table()  -> {ip: mac}
    interfaces()   -> [Iface]
    ping(ip, t)    -> bool
    is_physical(n) -> bool
"""

import ipaddress
import logging
from pathlib import Path

from app.network.common import Iface, normalize_mac, run, run_ok

log = logging.getLogger(__name__)

NAME = "linux"

# Interfaces que nunca son la LAN del evento
_SKIP = ("lo", "docker", "br-", "veth", "virbr", "tun", "tap", "proton", "wg",
         "zt", "ipv6leak")

# Prefijos de interfaz física (udev: wl* wifi, en*/eth* cable)
_PHYSICAL = ("wl", "en", "eth", "wlan")


def is_physical(name: str) -> bool:
    return name.startswith(_PHYSICAL)


# ── Tabla de vecinos ──────────────────────────────────────────────────────────


def neigh_table() -> dict[str, str]:
    """{ip: mac} del caché de vecinos del kernel, sin entradas incompletas."""
    table: dict[str, str] = {}
    for line in run(["ip", "-4", "neigh", "show"]).splitlines():
        parts = line.split()
        if not parts or "lladdr" not in parts:
            continue
        if "FAILED" in parts or "INCOMPLETE" in parts:
            continue
        mac = normalize_mac(parts[parts.index("lladdr") + 1])
        if mac:
            table[parts[0]] = mac
    return table or _proc_arp_table()


def _proc_arp_table() -> dict[str, str]:
    """Respaldo para sistemas sin iproute2: /proc/net/arp."""
    table: dict[str, str] = {}
    path = Path("/proc/net/arp")
    if not path.exists():
        return table
    for line in path.read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 4:
            mac = normalize_mac(fields[3])
            if mac:
                table[fields[0]] = mac
    return table


# ── Interfaces ────────────────────────────────────────────────────────────────


def interfaces() -> list[Iface]:
    out: list[Iface] = []
    for line in run(["ip", "-o", "-4", "addr", "show"]).splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        name, cidr = parts[1], parts[3]
        if name.startswith(_SKIP):
            continue
        try:
            iface = ipaddress.ip_interface(cidr)
        except ValueError:
            continue
        out.append(Iface(name, str(iface.ip), str(iface.network), _iface_mac(name)))
    return out


def _iface_mac(name: str) -> str | None:
    """MAC propia de la interfaz, desde sysfs."""
    try:
        return normalize_mac(Path(f"/sys/class/net/{name}/address").read_text())
    except OSError:
        return None


# ── Ping ──────────────────────────────────────────────────────────────────────


def ping(ip: str, timeout: int = 1) -> bool:
    """Un solo ping; su efecto útil es poblar la tabla ARP."""
    return run_ok(["ping", "-c", "1", "-n", "-W", str(timeout), ip],
                  timeout=timeout + 2)
