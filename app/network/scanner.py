"""
scanner.py — Barrido de la LAN para saber qué MAC están presentes.

Dos estrategias:
  arp-scan   más rápido y fiable, pero necesita el binario y privilegios de root
  ping sweep respaldo universal: hace ping en paralelo a toda la subred y luego
             lee la tabla ARP del kernel (no requiere privilegios)

Ambas devuelven {mac: ip}.
"""

import ipaddress
import logging
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from app.config import (
    NET_INTERFACE, NET_MAX_PARALLEL, NET_PING_TIMEOUT, NET_PREFER_ARP_SCAN,
    NET_SUBNET,
)
from app.network import arp

log = logging.getLogger(__name__)


class Scanner:
    def __init__(self):
        self.interface, self.subnet = arp.guess_target(NET_INTERFACE, NET_SUBNET)
        self.method = self._choose_method()
        log.info("Escáner de red: plataforma=%s interfaz=%s subred=%s método=%s",
                 arp.PLATFORM, self.interface, self.subnet, self.method)

    def _choose_method(self) -> str:
        # arp-scan es solo de Unix; en Windows ni se busca (os.geteuid tampoco
        # existe allí, así que la comprobación de root va aparte).
        if not NET_PREFER_ARP_SCAN or not shutil.which("arp-scan"):
            return "ping"
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return "arp-scan"
        log.info("arp-scan está instalado pero el servidor no corre como root; "
                 "se usará el barrido por ping.")
        return "ping"

    @property
    def usable(self) -> bool:
        return bool(self.subnet)

    # ── Estrategias ───────────────────────────────────────────────────────────

    def _arp_scan(self) -> dict[str, str]:
        cmd = ["arp-scan", "--quiet", "--plain", "--retry=2"]
        if self.interface:
            cmd.append(f"--interface={self.interface}")
        cmd.append(self.subnet)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {}
        found: dict[str, str] = {}
        for line in (proc.stdout or "").splitlines():
            fields = line.split()
            if len(fields) >= 2:
                mac = arp.normalize_mac(fields[1])
                if mac:
                    found[mac] = fields[0]
        return found

    def _ping_sweep(self) -> dict[str, str]:
        try:
            network = ipaddress.ip_network(self.subnet, strict=False)
        except ValueError:
            log.error("Subred inválida: %s", self.subnet)
            return {}

        hosts = [str(h) for h in network.hosts()]
        if len(hosts) > 1024:
            log.warning("Subred con %d hosts: se omite el barrido", len(hosts))
            return {}

        with ThreadPoolExecutor(max_workers=NET_MAX_PARALLEL) as pool:
            list(pool.map(lambda ip: arp.ping(ip, NET_PING_TIMEOUT), hosts))

        table = arp.neigh_table()
        return {mac: ip for ip, mac in table.items() if ip in set(hosts)}

    # ── API ───────────────────────────────────────────────────────────────────

    def scan(self) -> dict[str, str]:
        """{mac: ip} de los dispositivos vistos en este barrido."""
        if not self.usable:
            return {}
        try:
            found = self._arp_scan() if self.method == "arp-scan" else self._ping_sweep()
        except Exception:
            log.exception("Error durante el barrido de red")
            return {}

        # La tabla ARP puede tener entradas frescas de dispositivos que no
        # respondieron al ping (p. ej. con firewall): también cuentan como vistos.
        for ip, mac in arp.neigh_table().items():
            found.setdefault(mac, ip)
        return found
