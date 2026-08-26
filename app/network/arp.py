"""
arp.py — Resolución IP → MAC y descubrimiento de la red local.

Funciona sin ser router: basta con estar en el mismo segmento L2 que los
dispositivos. Si la IP no está todavía en la tabla ARP, se fuerza una entrada
enviando un ping y se reintenta.

Este módulo es la API pública; los comandos concretos de cada sistema viven en
platform_linux.py y platform_windows.py. Aquí solo quedan la caché, la
normalización y la lógica de decidir qué subred barrer, que son iguales en
todas partes.

Limitación conocida: si el punto de acceso tiene aislamiento de clientes
activado, el servidor no podrá resolver ni descubrir a los demás dispositivos.
"""

import ipaddress
import logging
import threading
import time

from app.network.common import IS_WINDOWS, Iface, normalize_mac  # noqa: F401

if IS_WINDOWS:
    from app.network import platform_windows as _sys
else:
    from app.network import platform_linux as _sys

log = logging.getLogger(__name__)

PLATFORM = _sys.NAME

# La tabla de vecinos se consulta en cada petición HTTP para identificar al
# cliente. Se cachea unos segundos para no lanzar un subproceso cada vez.
_CACHE_TTL = 3.0
_cache: dict[str, str] = {}
_cache_at = 0.0
_cache_lock = threading.Lock()

# Las interfaces propias cambian muy poco y en Windows averiguarlas cuesta un
# arranque de PowerShell (~1 s), así que se cachean bastante más tiempo.
_IFACE_TTL = 60.0
_ifaces: list[Iface] = []
_ifaces_at = 0.0
_ifaces_lock = threading.Lock()


# ── Tabla de vecinos ──────────────────────────────────────────────────────────


def neigh_table(max_age: float = 0.0) -> dict[str, str]:
    """
    {ip: mac} del caché de vecinos del sistema, sin entradas incompletas.
    Con max_age > 0 se acepta un resultado cacheado de hasta esa antigüedad.
    """
    global _cache, _cache_at
    if max_age > 0:
        with _cache_lock:
            if _cache and (time.monotonic() - _cache_at) < max_age:
                return dict(_cache)

    table = _sys.neigh_table()

    with _cache_lock:
        _cache = dict(table)
        _cache_at = time.monotonic()
    return table


def ping(ip: str, timeout: int = 1) -> bool:
    """Un solo ping; su efecto útil es poblar la tabla ARP."""
    return _sys.ping(ip, timeout)


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


def local_networks(max_age: float = _IFACE_TTL) -> list[Iface]:
    """Interfaces IPv4 candidatas a ser la LAN del evento."""
    global _ifaces, _ifaces_at
    with _ifaces_lock:
        if _ifaces and (time.monotonic() - _ifaces_at) < max_age:
            return list(_ifaces)

    found = []
    for iface in _sys.interfaces():
        try:
            network = ipaddress.ip_network(iface.cidr)
        except ValueError:
            continue
        if network.prefixlen < 22:
            # /21 o mayor: barrerla entera no es razonable
            log.warning("Se ignora %s (%s): subred demasiado grande",
                        iface.name, iface.cidr)
            continue
        found.append(iface)

    with _ifaces_lock:
        _ifaces = list(found)
        _ifaces_at = time.monotonic()
    return found


def local_ip_macs() -> dict[str, str]:
    """
    {ip_propia: mac} de esta máquina.

    Hace falta porque un equipo no tiene entrada ARP de sí mismo: sin esto,
    abrir la web en el propio servidor usando su IP de la LAN daría «no se pudo
    determinar la dirección MAC». Es el caso habitual del organizador, que
    trabaja en la misma máquina que hace de servidor.
    """
    return {iface.ip: iface.mac for iface in local_networks() if iface.mac}


def primary_local_mac() -> str | None:
    """MAC de la interfaz que da a la LAN del evento, si se pudo averiguar."""
    ifaces = local_networks()
    for iface in ifaces:
        if iface.mac and _sys.is_physical(iface.name):
            return iface.mac
    return next((iface.mac for iface in ifaces if iface.mac), None)


def guess_target(interface: str = "auto", subnet: str = "auto") -> tuple[str | None, str | None]:
    """Decide qué interfaz y subred barrer a partir de la configuración."""
    nets = local_networks()

    if subnet != "auto" and interface != "auto":
        return interface, subnet

    if interface != "auto":
        for iface in nets:
            if iface.name == interface:
                return iface.name, (subnet if subnet != "auto" else iface.cidr)
        return interface, (None if subnet == "auto" else subnet)

    if subnet != "auto":
        try:
            network = ipaddress.ip_network(subnet, strict=False)
        except ValueError:
            log.error("network.subnet no es una subred válida: %r", subnet)
            return None, None
        for iface in nets:
            if ipaddress.ip_address(iface.ip) in network:
                return iface.name, subnet
        return (nets[0].name if nets else None), subnet

    if not nets:
        return None, None
    # Preferir una interfaz física (wifi/ethernet) sobre el resto
    for iface in nets:
        if _sys.is_physical(iface.name):
            return iface.name, iface.cidr
    return nets[0].name, nets[0].cidr
