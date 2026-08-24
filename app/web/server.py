"""
server.py — Arranque del servidor y control de acceso por MAC.

Toda petición pasa por _gate(): se resuelve la MAC del cliente y, si no está en
la whitelist, se le niega el acceso al sitio entero mostrándole su propia MAC
para que el organizador pueda darla de alta.
"""

import logging
import time
import tomllib
from dataclasses import dataclass

from app import problems
from app.auth import Auth
from app.network.arp import normalize_mac
from app.config import (
    DEVICES_FILE, EVENT_TITLE, HOST, MAX_UPLOAD_BYTES, PORT,
    SUBMISSIONS_OPEN_DEFAULT,
)
from app.db import Database
from app.network.monitor import PresenceMonitor
from app.queue import RunQueue
from app.runner import docker_available
from app.web import html as H
from app.web import routes_admin, routes_team
from app.web.http import Router, html, serve

log = logging.getLogger(__name__)

SETTING_OPEN = "submissions_open"

# Rutas accesibles sin estar en la whitelist
PUBLIC_PATHS = {"/static/style.css"}


@dataclass
class Context:
    db: Database
    runq: RunQueue
    auth: Auth
    monitor: PresenceMonitor | None
    _docker: tuple[bool, str] = (False, "")
    _docker_at: float = 0.0

    def submissions_open(self) -> bool:
        value = self.db.get_setting(SETTING_OPEN)
        if value is None:
            return SUBMISSIONS_OPEN_DEFAULT
        return value == "1"

    @property
    def docker_status(self) -> tuple[bool, str]:
        """Estado de Docker, revisado como mucho una vez cada 30 s."""
        now = time.monotonic()
        if now - self._docker_at > 30:
            self._docker = docker_available()
            self._docker_at = now
        return self._docker


# ── Control de acceso ─────────────────────────────────────────────────────────


def _denied_page(client, reason: str, detail: str = "") -> str:
    mac = client.mac or "no se pudo determinar"
    body = (
        "<h1>Acceso no autorizado</h1>"
        + H.alert(reason, "err")
        + H.card("Datos de tu dispositivo", H.table(
            ["Dato", "Valor"],
            [["Dirección MAC", f'<code>{H.esc(mac)}</code>'],
             ["Dirección IP", f'<code>{H.esc(client.ip)}</code>']],
        ))
        + f'<p class="sub">{H.esc(detail)}</p>'
    )
    return H.page("Acceso denegado", body, None)


def make_gate(ctx: Context):
    def gate(request):
        if request.path in PUBLIC_PATHS:
            request.client = None
            return None

        client = ctx.auth.identify(request.client_ip, request.cookies)
        request.client = client

        if not client.mac:
            return html(_denied_page(
                client,
                "No se pudo determinar la dirección MAC de tu dispositivo.",
                "Esto ocurre si no estás en el mismo segmento de red que el "
                "servidor, o si el punto de acceso tiene aislamiento de clientes "
                "activado. Avisa al organizador.",
            ), 403)

        if not client.known:
            return html(_denied_page(
                client,
                "Tu dispositivo no está registrado en este evento.",
                "Dale la dirección MAC de arriba al organizador para que la añada "
                "a la lista de dispositivos autorizados de tu equipo.",
            ), 403)

        is_admin_path = request.path.startswith("/admin")

        if client.team is None and not is_admin_path:
            if client.is_admin_device:
                from app.web.http import redirect
                return redirect("/admin")
            return html(_denied_page(
                client,
                "Tu dispositivo está registrado pero no pertenece a ningún equipo.",
                "Pide al organizador que lo asigne a tu equipo.",
            ), 403)

        return None

    return gate


# ── Carga inicial de equipos y dispositivos ───────────────────────────────────


def load_devices_file(db: Database) -> int:
    """
    Precarga equipos y MAC desde devices.toml (opcional). Es aditivo: no borra
    nada de lo que ya haya en la base de datos.
    """
    if not DEVICES_FILE.exists():
        return 0
    try:
        with open(DEVICES_FILE, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        log.error("No se pudo leer %s: %s", DEVICES_FILE.name, e)
        return 0

    count = 0
    for entry in data.get("teams", []):
        name = entry.get("name")
        if not name:
            continue
        team_id = db.create_team(name)
        for device in entry.get("devices", []):
            mac = normalize_mac(device.get("mac", ""))
            if not mac:
                log.warning("MAC inválida en devices.toml: %r", device.get("mac"))
                continue
            db.add_device(mac, team_id, device.get("label", ""),
                          is_admin=bool(device.get("admin", False)))
            count += 1

    for device in data.get("admins", []):
        mac = normalize_mac(device.get("mac", ""))
        if not mac:
            continue
        db.add_device(mac, None, device.get("label", "admin"), is_admin=True)
        count += 1

    if count:
        log.info("devices.toml: %d dispositivo(s) precargados", count)
    return count


# ── Arranque ──────────────────────────────────────────────────────────────────


def build() -> tuple[Router, Context]:
    db = Database()
    load_devices_file(db)

    problems.load_all(db)
    if not problems.all_problems():
        log.warning("No se cargó ningún problema desde problems/")

    docker_ok, docker_info = docker_available()
    if docker_ok:
        log.info("Docker disponible (servidor %s)", docker_info)
    else:
        log.error("Docker NO disponible: %s — las evaluaciones fallarán", docker_info)

    monitor = PresenceMonitor(db)
    auth = Auth(db, monitor)
    runq = RunQueue(db)

    ctx = Context(db=db, runq=runq, auth=auth, monitor=monitor)

    router = Router()
    routes_team.register(router, ctx)
    routes_admin.register(router, ctx)

    runq.start()
    monitor.start()
    return router, ctx


def run():
    router, ctx = build()
    server = serve(router, HOST, PORT, before_request=make_gate(ctx),
                   max_body=MAX_UPLOAD_BYTES + 1024 * 1024)

    urls = _local_urls()
    log.info("═" * 62)
    log.info("  %s — servidor listo", EVENT_TITLE)
    for url in urls:
        log.info("  Equipos: %s", url)
        log.info("  Admin:   %s/admin", url)
    log.info("═" * 62)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Parando...")
    finally:
        ctx.runq.stop()
        if ctx.monitor:
            ctx.monitor.stop()
        server.shutdown()
        server.server_close()


def _local_urls() -> list[str]:
    from app.network.arp import local_networks
    if HOST not in ("0.0.0.0", "::"):
        return [f"http://{HOST}:{PORT}"]
    urls = [f"http://{ip}:{PORT}" for _, ip, _ in local_networks()]
    return urls or [f"http://localhost:{PORT}"]
