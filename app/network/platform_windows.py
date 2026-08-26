"""
platform_windows.py — Backend de red para Windows.

Equivalencias con el backend de Linux:

    ip -4 neigh show      →  arp -a              (respaldo: Get-NetNeighbor)
    ip -o -4 addr show    →  Get-NetIPAddress    (respaldo: route print -4)
    ping -c 1 -n -W 1     →  ping -n 1 -w 1000 -4

Dos cuidados propios de Windows:

  * Todos los comandos pasan por common.run(), que usa CREATE_NO_WINDOW. Sin
    eso, un barrido de una /24 abriría 254 ventanas de consola parpadeando.
  * Nada de parsear rótulos: la salida de `arp -a`, `ipconfig` y `route print`
    está traducida al idioma del sistema. Solo se leen columnas numéricas
    (IP, máscara, MAC), que son iguales en todos los idiomas.

Implementa el mismo contrato que platform_linux.py.
"""

import ipaddress
import logging
import re
import shutil

from app.network.common import Iface, normalize_mac, run, run_ok

log = logging.getLogger(__name__)

NAME = "windows"

# Adaptadores que nunca son la LAN del evento (subcadena, en minúsculas).
# Ojo al orden: "vEthernet (WSL)" contiene "ethernet", por eso esta lista se
# aplica ANTES de la preferencia por interfaz física.
_SKIP = ("loopback", "pseudo-interface", "vethernet", "virtualbox", "vmware",
         "hyper-v", "bluetooth", "teredo", "isatap", "docker", "wsl",
         "tailscale", "zerotier", "proton", "wireguard", "tap-windows",
         "openvpn", "nordlynx", "npcap", "vpn")

# Nombre sintético que usa el respaldo por `route print`, donde no hay nombre
# de adaptador pero sí sabemos que es la interfaz de la ruta por defecto.
DEFAULT_ROUTE = "(ruta por defecto)"

_PHYSICAL = ("wi-fi", "wifi", "wlan", "ethernet", "lan", DEFAULT_ROUTE)


def is_physical(name: str) -> bool:
    lower = name.lower()
    return any(token in lower for token in _PHYSICAL)


def _is_skipped(name: str) -> bool:
    lower = name.lower()
    return any(token in lower for token in _SKIP)


# ── PowerShell ────────────────────────────────────────────────────────────────


def _powershell(script: str, timeout: float = 20.0) -> str:
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if not exe:
        return ""
    return run([exe, "-NoProfile", "-NonInteractive", "-Command", script],
               timeout=timeout)


# ── Tabla de vecinos ──────────────────────────────────────────────────────────

# Fila de `arp -a`:  "  172.17.67.1           00-00-5e-00-01-20     dinámico"
_ARP_ROW = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})\b"
)


def parse_arp_a(output: str) -> dict[str, str]:
    """{ip: mac} a partir de la salida de `arp -a`."""
    table: dict[str, str] = {}
    for line in output.splitlines():
        match = _ARP_ROW.match(line)
        if not match:
            continue
        ip_text, raw_mac = match.groups()
        # La difusión (ff-ff-...) la descarta normalize_mac; la multidifusión
        # tiene MAC válida (01-00-5e-...) pero no es ningún dispositivo.
        mac = normalize_mac(raw_mac)
        if not mac:
            continue
        try:
            address = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if address.is_multicast or address.is_loopback or address.is_unspecified:
            continue
        table[ip_text] = mac
    return table


def parse_net_neighbor(output: str) -> dict[str, str]:
    """{ip: mac} a partir de las líneas 'ip|mac' que emite _NEIGH_PS."""
    table: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 2:
            continue
        mac = normalize_mac(parts[1])
        if mac:
            table[parts[0].strip()] = mac
    return table


_NEIGH_PS = (
    "$ErrorActionPreference='SilentlyContinue';"
    "Get-NetNeighbor -AddressFamily IPv4 |"
    " Where-Object { $_.LinkLayerAddress -and $_.State -ne 'Unreachable' } |"
    " ForEach-Object { \"$($_.IPAddress)|$($_.LinkLayerAddress)\" }"
)


def neigh_table() -> dict[str, str]:
    """{ip: mac} del caché ARP de Windows."""
    table = parse_arp_a(run(["arp", "-a"]))
    if table:
        return table
    # `arp -a` puede no devolver nada si el caché está vacío del todo.
    return parse_net_neighbor(_powershell(_NEIGH_PS))


# ── Interfaces ────────────────────────────────────────────────────────────────

_IFACE_PS = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$ad=@{};"
    "Get-NetAdapter | ForEach-Object { $ad[[int]$_.ifIndex]=$_ };"
    "Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object {"
    " $a=$ad[[int]$_.InterfaceIndex];"
    " ($_.InterfaceAlias,$_.IPAddress,$_.PrefixLength,$a.MacAddress,$a.Status)"
    " -join '|' }"
)


def parse_powershell_ifaces(output: str) -> list[Iface]:
    """[Iface] a partir de las líneas 'nombre|ip|prefijo|mac|estado'."""
    out: list[Iface] = []
    for line in output.splitlines():
        fields = [f.strip() for f in line.strip().split("|")]
        if len(fields) < 5:
            continue
        name, ip_text, prefix, raw_mac, status = fields[:5]
        # Estado vacío = el adaptador no salió en Get-NetAdapter (loopback y
        # pseudo-interfaces); se filtran luego por nombre.
        if status and status.lower() != "up":
            continue
        try:
            iface = ipaddress.ip_interface(f"{ip_text}/{prefix}")
        except ValueError:
            continue
        if iface.ip.is_loopback or iface.ip.is_link_local:
            continue  # 169.254.x.x = el DHCP falló
        out.append(Iface(name, str(iface.ip), str(iface.network),
                         normalize_mac(raw_mac)))
    return out


def parse_route_print(output: str) -> list[Iface]:
    """
    [Iface] a partir de `route print -4`, para cuando no hay PowerShell.

    No mira ni una sola palabra de la salida, solo columnas numéricas. La ruta
    por defecto (0.0.0.0/0) revela la IP de la interfaz que sale a la LAN, y la
    ruta on-link que contiene esa IP da la subred.

    Las columnas se cuentan desde el final porque la de la puerta de enlace
    está traducida y no siempre ocupa un solo campo: en inglés es "On-link"
    (una palabra) y en español "En vínculo" (dos). Destino y máscara son
    siempre las dos primeras, e interfaz y métrica las dos últimas.
    """
    default_ip: str | None = None
    networks: list[tuple[str, str]] = []  # (ip_interfaz, cidr)

    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5 or not fields[-1].lstrip("-").isdigit():
            continue
        destination, netmask, interface_ip = fields[0], fields[1], fields[-2]
        try:
            network = ipaddress.ip_network(f"{destination}/{netmask}", strict=False)
            local = ipaddress.ip_address(interface_ip)
        except ValueError:
            continue
        if local.is_loopback or local.is_link_local:
            continue
        if network.prefixlen == 0:
            default_ip = interface_ip
            continue
        if network.prefixlen == 32 or network.is_multicast:
            continue
        if local in network:
            networks.append((interface_ip, str(network)))

    # La interfaz de la ruta por defecto va primero: es la de la LAN.
    networks.sort(key=lambda item: item[0] != default_ip)
    return [
        Iface(DEFAULT_ROUTE if ip == default_ip else "(desconocida)", ip, cidr, None)
        for ip, cidr in networks
    ]


def interfaces() -> list[Iface]:
    found = parse_powershell_ifaces(_powershell(_IFACE_PS))
    if not found:
        log.debug("PowerShell no devolvió interfaces; se prueba con route print")
        found = parse_route_print(run(["route", "print", "-4"], timeout=10))
    return [iface for iface in found if not _is_skipped(iface.name)]


# ── Ping ──────────────────────────────────────────────────────────────────────


def ping(ip: str, timeout: int = 1) -> bool:
    """Un solo ping; su efecto útil es poblar el caché ARP."""
    milliseconds = max(200, int(timeout * 1000))
    return run_ok(["ping", "-n", "1", "-w", str(milliseconds), "-4", ip],
                  timeout=timeout + 2)
