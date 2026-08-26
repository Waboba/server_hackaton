"""
auth.py — Identificación por MAC y sesión de administrador.

Cada petición HTTP se resuelve así:
    IP del socket → MAC (tabla ARP) → dispositivo en la whitelist → equipo

Una MAC que no esté en la whitelist no puede usar nada del sitio: se le
muestra su propia MAC para que el organizador pueda darla de alta, y queda
registrada en unknown_devices.

ADVERTENCIA: una MAC se falsifica en segundos. Esto es control de acceso de
conveniencia dentro de una red controlada, no una medida de seguridad. Por eso
el panel de administración exige además una contraseña.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass

from app.config import (
    ADMIN_PASSWORD, ADMIN_REQUIRE_MAC, ADMIN_SESSION_HOURS, NET_DEV_MAC,
)
from app.network import arp

log = logging.getLogger(__name__)

SESSION_COOKIE = "hack_admin"
_SECRET_KEY = "admin_session_secret"
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@dataclass
class Client:
    ip: str
    mac: str | None = None
    device: dict | None = None
    team: dict | None = None
    admin_session: bool = False

    @property
    def known(self) -> bool:
        return self.device is not None

    @property
    def is_admin_device(self) -> bool:
        return bool(self.device and self.device.get("is_admin"))

    @property
    def is_admin(self) -> bool:
        """Admin efectivo: sesión válida y, si se exige, MAC marcada como admin."""
        if not self.admin_session:
            return False
        return self.is_admin_device or not ADMIN_REQUIRE_MAC

    @property
    def team_id(self) -> int | None:
        return self.team["id"] if self.team else None

    @property
    def team_name(self) -> str:
        return self.team["name"] if self.team else "—"

    @property
    def device_label(self) -> str:
        if not self.device:
            return self.mac or self.ip
        return self.device.get("label") or self.mac or self.ip


class Auth:
    def __init__(self, db, monitor=None):
        self.db = db
        self.monitor = monitor
        self.secret = self._load_secret()

    def _load_secret(self) -> bytes:
        value = self.db.get_setting(_SECRET_KEY)
        if not value:
            value = secrets.token_hex(32)
            self.db.set_setting(_SECRET_KEY, value)
        return value.encode()

    # ── Resolución de la petición ─────────────────────────────────────────────

    def resolve_mac(self, ip: str) -> str | None:
        # Un equipo no tiene entrada ARP de sí mismo, así que las peticiones
        # del propio servidor (por localhost o por su IP de la LAN) hay que
        # resolverlas con sus interfaces. Pasa constantemente: el organizador
        # suele abrir el panel en la misma máquina que sirve la web.
        if ip in _LOOPBACK:
            return NET_DEV_MAC or arp.primary_local_mac()
        own = arp.local_ip_macs()
        if ip in own:
            return NET_DEV_MAC or own[ip]
        return arp.mac_for_ip(ip)

    def identify(self, ip: str, cookies: dict) -> Client:
        mac = self.resolve_mac(ip)
        client = Client(ip=ip, mac=mac)

        if mac:
            device = self.db.get_device(mac)
            if device:
                client.device = device
                if device.get("team_id"):
                    client.team = self.db.get_team(device["team_id"])
                if self.monitor:
                    try:
                        self.monitor.note_http_activity(mac, ip)
                    except Exception:
                        log.exception("Fallo registrando actividad HTTP de %s", mac)
                else:
                    self.db.touch_device(mac, ip)
            else:
                self.db.touch_unknown(mac, ip, source="http")

        token = cookies.get(SESSION_COOKIE)
        if token and self.check_token(token, mac):
            client.admin_session = True

        return client

    # ── Sesión de admin ───────────────────────────────────────────────────────

    def login(self, password: str, client: Client) -> tuple[bool, str]:
        if not ADMIN_PASSWORD or ADMIN_PASSWORD == "cambiame":
            log.warning("La contraseña de admin sigue siendo la de ejemplo")
        if not hmac.compare_digest(password, ADMIN_PASSWORD):
            return False, "Contraseña incorrecta."
        if ADMIN_REQUIRE_MAC and not client.is_admin_device:
            return False, (
                "Este dispositivo no está marcado como administrador. "
                "Marca su MAC como admin desde otro dispositivo de admin, o "
                "desactiva admin.require_admin_mac en config.toml."
            )
        return True, self.make_token(client.mac)

    def make_token(self, mac: str | None) -> str:
        expires = int(time.time()) + ADMIN_SESSION_HOURS * 3600
        payload = f"{mac or '-'}|{expires}"
        signature = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        raw = f"{payload}|{signature}".encode()
        return base64.urlsafe_b64encode(raw).decode()

    def check_token(self, token: str, mac: str | None) -> bool:
        try:
            raw = base64.urlsafe_b64decode(token.encode()).decode()
            token_mac, expires_str, signature = raw.split("|")
        except Exception:
            return False

        payload = f"{token_mac}|{expires_str}"
        expected = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        try:
            if int(expires_str) < time.time():
                return False
        except ValueError:
            return False
        # La sesión queda ligada a la MAC con la que se inició
        if ADMIN_REQUIRE_MAC and token_mac != (mac or "-"):
            return False
        return True
