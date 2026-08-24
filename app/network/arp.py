"""
arp.py — Resolución IP → MAC leyendo la tabla de vecinos del kernel.

Funciona sin ser router: basta con estar en el mismo segmento L2 que los
dispositivos. Si la IP no está todavía en la tabla ARP, se fuerza una entrada
enviando un ping y se reintenta.

Limitación conocida: si el punto de acceso tiene aislamiento de clientes
activado, el servidor no podrá resolver ni descubrir a los demás dispositivos.
"""

import ipaddress
import logging
import re
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

# La tabla de vecinos se consulta en cada petición HTTP para identificar al
# cliente. Se cachea unos segundos para no lanzar un subproceso cada vez.
_CACHE_TTL = 3.0
_cache: dict[str, str] = {}
_cache_at = 0.0
_cache_lock = threading.Lock()

MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_INCOMPLETE = {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}


def normalize_mac(mac: str | None) -> str | None:
    """Normaliza a 'aa:bb:cc:dd:ee:ff'. Devuelve None si no es una MAC válida."""
    if not mac:
        return None
    value = mac.strip().lower().replace("-", ":").replace(".", ":")
    if ":" not in value and len(value) == 12:
        value = ":".join(value[i:i + 2] for i in range(0, 12, 2))
    if not MAC_RE.match(value) or value in _INCOMPLETE:
        return None
    return value


def _run(cmd: list[str], timeout: int = 5) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""


def neigh_table(max_age: float = 0.0) -> dict[str, str]:
    """
    {ip: mac} desde `ip neigh show`, descartando entradas incompletas.
    Con max_age > 0 se acepta un resultado cacheado de hasta esa antigüedad.
    """
    global _cache, _cache_at
    if max_age > 0:
        with _cache_lock:
            if _cache and (time.monotonic() - _cache_at) < max_age:
                return dict(_cache)

    table = _read_neigh_table()

    with _cache_lock:
        _cache = dict(table)
        _cache_at = time.monotonic()
    return table


def _read_neigh_table() -> dict[str, str]:
    table: dict[str, str] = {}
    out = _run(["ip", "-4", "neigh", "show"])
    for line in out.splitlines():
        parts = line.split()
        if not parts or "lladdr" not in parts:
            continue
        if "FAILED" in parts or "INCOMPLETE" in parts:
            continue
        ip = parts[0]
        mac = normalize_mac(parts[parts.index("lladdr") + 1])
        if mac:
            table[ip] = mac
    if table:
        return table
    return _proc_arp_table()


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


def ping(ip: str, timeout: int = 1) -> bool:
    """Un solo ping; su efecto útil es poblar la tabla ARP."""
    try:
        proc = subprocess.run(
            ["ping", "-c", "1", "-n", "-W", str(timeout), ip],
            capture_output=True, timeout=timeout + 2,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def mac_for_ip(ip: str, probe: bool = True) -> str | None:
    """MAC de una IP. Si no está en la tabla, la sondea con un ping."""
    if not ip:
        return None
    mac = neigh_table(max_age=_CACHE_TTL).get(ip)
    if mac:
        return mac
    # Sin caché por si la entrada acaba de aparecer
    mac = neigh_table().get(ip)
    if mac or not probe:
        return mac
    ping(ip)
    return neigh_table().get(ip)


# ── Interfaces locales ────────────────────────────────────────────────────────

# Interfaces que nunca son la LAN del evento
_SKIP_IFACES = ("lo", "docker", "br-", "veth", "virbr", "tun", "tap", "proton",
                "wg", "zt", "ipv6leak")


def local_networks() -> list[tuple[str, str, str]]:
    """[(interfaz, ip, cidr)] de las interfaces IPv4 candidatas."""
    out = []
    for line in _run(["ip", "-o", "-4", "addr", "show"]).splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface, cidr = parts[1], parts[3]
        if iface.startswith(_SKIP_IFACES):
            continue
        try:
            interface = ipaddress.ip_interface(cidr)
        except ValueError:
            continue
        if interface.network.prefixlen < 22:
            # /21 o mayor: barrerla entera no es razonable
            log.warning("Se ignora %s (%s): subred demasiado grande", iface, cidr)
            continue
        out.append((iface, str(interface.ip), str(interface.network)))
    return out


def guess_target(interface: str = "auto", subnet: str = "auto") -> tuple[str | None, str | None]:
    """Decide qué interfaz y subred barrer a partir de la configuración."""
    nets = local_networks()

    if subnet != "auto" and interface != "auto":
        return interface, subnet

    if interface != "auto":
        for iface, _, cidr in nets:
            if iface == interface:
                return iface, (subnet if subnet != "auto" else cidr)
        return interface, (None if subnet == "auto" else subnet)

    if subnet != "auto":
        try:
            network = ipaddress.ip_network(subnet, strict=False)
        except ValueError:
            return None, None
        for iface, ip, _ in nets:
            if ipaddress.ip_address(ip) in network:
                return iface, subnet
        return (nets[0][0] if nets else None), subnet

    if not nets:
        return None, None
    # Preferir una interfaz física (wifi/ethernet) sobre el resto
    for iface, _, cidr in nets:
        if iface.startswith(("wl", "en", "eth", "wlan")):
            return iface, cidr
    return nets[0][0], nets[0][2]
