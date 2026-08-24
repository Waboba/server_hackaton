"""
monitor.py — Vigila la presencia de los dispositivos de la whitelist y genera
los eventos de conexión y desconexión.

Se usa histéresis: un dispositivo pasa a 'offline' solo después de
NET_MISS_THRESHOLD barridos consecutivos sin verlo (por defecto 3 × 20 s ≈ 1
minuto). Sin esto, el ahorro de energía del WiFi provocaría desconexiones
falsas constantemente.

Los eventos se guardan en device_events (historial permanente) y se publican
en el canal SSE 'admin' — nunca en el de los equipos.
"""

import logging
import threading

from app import events
from app.config import NET_ENABLED, NET_MISS_THRESHOLD, NET_SCAN_INTERVAL
from app.network.scanner import Scanner

log = logging.getLogger(__name__)


class PresenceMonitor:
    def __init__(self, db):
        self.db = db
        self.scanner = Scanner()
        self._misses: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_scan_at: str | None = None
        self.last_scan_count: int = 0

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    def start(self):
        if not NET_ENABLED:
            log.warning("Monitor de red desactivado en config.toml")
            return
        if not self.scanner.usable:
            log.error(
                "No se pudo determinar la subred a barrer. Configura "
                "network.subnet en config.toml (p. ej. \"192.168.10.0/24\")."
            )
            return
        self._thread = threading.Thread(target=self._loop, name="net-monitor",
                                        daemon=True)
        self._thread.start()
        log.info("Monitor de presencia arrancado (intervalo %ds, umbral %d fallos)",
                 NET_SCAN_INTERVAL, NET_MISS_THRESHOLD)

    def stop(self):
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── Bucle ─────────────────────────────────────────────────────────────────

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("Error en el barrido de presencia")
            self._stop.wait(NET_SCAN_INTERVAL)

    def _tick(self):
        from app.db import now_iso

        seen = self.scanner.scan()
        self.last_scan_at = now_iso()
        self.last_scan_count = len(seen)

        known = {d["mac"]: d for d in self.db.list_devices()}

        for mac, ip in seen.items():
            device = known.get(mac)
            if device is None:
                self.db.touch_unknown(mac, ip, source="scan")
                continue
            self._misses[mac] = 0
            if device["status"] != "online":
                self._transition(device, "connect", ip)
            else:
                self.db.touch_device(mac, ip)

        for mac, device in known.items():
            if mac in seen:
                continue
            self._misses[mac] = self._misses.get(mac, 0) + 1
            if (device["status"] == "online"
                    and self._misses[mac] >= NET_MISS_THRESHOLD):
                self._transition(device, "disconnect", device.get("last_ip"))

        events.publish_admin("devices_refreshed", {
            "online": sum(1 for m in known if self._misses.get(m, 0) == 0
                          and m in seen),
            "scanned": len(seen),
            "at": self.last_scan_at,
        })

    # ── Transiciones ──────────────────────────────────────────────────────────

    def _transition(self, device: dict, event: str, ip: str | None):
        mac = device["mac"]
        status = "online" if event == "connect" else "offline"
        self.db.set_device_status(mac, status, ip)
        record = self.db.add_device_event(
            mac=mac, event=event, ip=ip,
            team_id=device.get("team_id"),
            team_name=device.get("team_name"),
            label=device.get("label"),
        )
        who = device.get("team_name") or "sin equipo"
        name = device.get("label") or mac
        log.info("%s: %s (%s)", "CONEXIÓN" if event == "connect" else "DESCONEXIÓN",
                 name, who)
        # Solo el canal de admin recibe información de red.
        events.publish_admin("device_event", record)

    # ── Señales desde la web ──────────────────────────────────────────────────

    def note_http_activity(self, mac: str, ip: str | None):
        """
        Una petición HTTP de un dispositivo es prueba directa de que está
        conectado: permite detectar la conexión al instante, sin esperar al
        siguiente barrido.
        """
        device = self.db.get_device(mac)
        if not device:
            return
        self._misses[mac] = 0
        if device["status"] != "online":
            self._transition(device, "connect", ip)
        else:
            self.db.touch_device(mac, ip)
