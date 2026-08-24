"""
routes_admin.py — Panel de administración.

Es el único sitio donde se expone la información de red: lista de dispositivos
conectados, avisos de conexión y desconexión, e historial completo. Todas las
rutas de este módulo exigen sesión de admin (y, si require_admin_mac está
activo, una MAC marcada como administradora).
"""

import logging

from app import problems
from app.auth import SESSION_COOKIE
from app.config import ADMIN_REQUIRE_MAC, ADMIN_SESSION_HOURS, EVENT_TITLE
from app.db import ago, fmt_local
from app.network import arp
from app.queue import PHASE_LABELS
from app.web import html as H
from app.web.http import Response, html, redirect
from app.web.routes_team import _sse

log = logging.getLogger(__name__)

SETTING_OPEN = "submissions_open"


def register(router, ctx):
    db, runq, auth = ctx.db, ctx.runq, ctx.auth

    def guard(request):
        """Devuelve None si puede pasar, o la respuesta de rechazo."""
        if request.client.is_admin:
            return None
        return redirect("/admin/login")

    # ── Login ─────────────────────────────────────────────────────────────────

    @router.get("/admin/login")
    def login_form(request):
        client = request.client
        if client.is_admin:
            return redirect("/admin")

        hint = ""
        if ADMIN_REQUIRE_MAC and not client.is_admin_device:
            hint = H.alert(
                "Este dispositivo no está marcado como administrador. Marca su MAC "
                f"({client.mac or 'desconocida'}) como admin desde otro dispositivo "
                "de admin, o desactiva admin.require_admin_mac en config.toml.",
                "warn",
            )

        body = (
            "<h1>Panel de administración</h1>"
            + hint
            + H.alert(request.get("msg"), "err")
            + H.card("", '<form method="post" action="/admin/login">'
                         "<label>Contraseña</label>"
                         '<input type="password" name="password" autofocus required>'
                         '<p style="margin-top:16px"><button>Entrar</button></p></form>')
        )
        return html(H.page("Admin", body, client))

    @router.post("/admin/login")
    def login(request):
        ok, result = auth.login(request.get("password"), request.client)
        if not ok:
            log.warning("Intento de login de admin fallido desde %s (%s)",
                        request.client_ip, request.client.mac)
            return redirect("/admin/login", result)
        response = redirect("/admin")
        response.set_cookie(SESSION_COOKIE, result, max_age=ADMIN_SESSION_HOURS * 3600)
        log.info("Admin autenticado desde %s (%s)", request.client_ip, request.client.mac)
        return response

    @router.post("/admin/logout")
    def logout(request):
        response = redirect("/")
        response.set_cookie(SESSION_COOKIE, "", max_age=0)
        return response

    # ── Resumen ───────────────────────────────────────────────────────────────

    @router.get("/admin")
    def dashboard(request):
        if (block := guard(request)):
            return block
        client = request.client
        stats = db.stats()
        monitor = ctx.monitor

        cards = "".join([
            H.stat(stats["teams"], "equipos"),
            H.stat(f'{stats["devices_online"]}/{stats["devices_total"]}',
                   "dispositivos conectados"),
            H.stat(stats["run_queued"] + stats["run_running"], "runs en cola/curso"),
            H.stat(stats["run_done"], "runs completados"),
            H.stat(stats["run_error"], "runs con error"),
            H.stat(stats["unknown"], "MAC desconocidas"),
        ])

        open_now = ctx.submissions_open()
        toggle = H.post_button(
            "/admin/submissions",
            "Cerrar entregas" if open_now else "Abrir entregas",
            {"open": "0" if open_now else "1"},
            css="small" if not open_now else "small danger",
        )
        estado = H.badge("ABIERTAS", "ok") if open_now else H.badge("CERRADAS", "err")

        if monitor and monitor.running:
            net = (f'{H.badge("activo", "ok")} · interfaz '
                   f'<code>{H.esc(monitor.scanner.interface)}</code> · subred '
                   f'<code>{H.esc(monitor.scanner.subnet)}</code> · método '
                   f'<code>{H.esc(monitor.scanner.method)}</code> · último barrido '
                   f'{H.esc(ago(monitor.last_scan_at))} '
                   f'({monitor.last_scan_count} dispositivos vistos)')
        else:
            net = (H.badge("detenido", "err")
                   + ' <span class="muted">— no se detectarán conexiones ni '
                     "desconexiones. Revisa la sección [network] de config.toml.</span>")

        docker_ok, docker_info = ctx.docker_status
        docker = (H.badge("ok", "ok") + f' <span class="muted">servidor {H.esc(docker_info)}</span>'
                  if docker_ok else
                  H.badge("no disponible", "err") + f' <span class="muted">{H.esc(docker_info)}</span>')

        recent = db.list_device_events(limit=12)
        feed = _events_feed(recent)

        queue_rows = _queue_rows(db)
        errors = problems.load_errors()
        error_block = ""
        if errors:
            error_block = H.card("Problemas que no cargaron", H.table(
                ["Carpeta", "Error"],
                [[H.esc(k), H.esc(v)] for k, v in errors.items()],
            ))

        body = (
            H.alert(request.get("msg"), "info")
            + f"<h1>{H.esc(EVENT_TITLE)} · administración</h1>"
            + f'<div class="grid" style="margin-bottom:18px">{cards}</div>'
            + H.card("Estado del evento",
                     f"<p>Entregas: {estado} &nbsp; {toggle}</p>"
                     f"<p>Monitor de red: {net}</p>"
                     f"<p>Docker: {docker}</p>")
            + error_block
            + H.card("Últimas conexiones y desconexiones",
                     feed + '<p style="margin-top:12px">'
                            '<a href="/admin/events">Ver historial completo</a></p>')
            + H.card("Cola de evaluación",
                     H.table(["Run", "Equipo", "Problema", "Estado", "Fase", "Desde"],
                             queue_rows, "La cola está vacía."))
        )
        return html(H.page("Resumen", body, client, "/admin", admin=True,
                           extra_head=_admin_live()))

    @router.post("/admin/submissions")
    def toggle_submissions(request):
        if (block := guard(request)):
            return block
        value = request.get("open") == "1"
        db.set_setting(SETTING_OPEN, "1" if value else "0")
        return redirect("/admin", "Entregas abiertas." if value else "Entregas cerradas.")

    # ── Dispositivos ──────────────────────────────────────────────────────────

    @router.get("/admin/devices")
    def devices(request):
        if (block := guard(request)):
            return block
        client = request.client
        teams = db.list_teams()

        rows = []
        for device in db.list_devices():
            actions = H.post_button("/admin/devices/delete", "Eliminar",
                                    {"mac": device["mac"]}, css="small danger",
                                    confirm=f"¿Eliminar {device['mac']}?")
            admin_flag = (H.badge("admin", "warn") if device["is_admin"] else "")
            rows.append([
                H.online_dot(device["status"]),
                f'<code>{H.esc(device["mac"])}</code>',
                H.esc(device["label"] or "—"),
                H.esc(device["team_name"] or "—") + " " + admin_flag,
                f'<code>{H.esc(device["last_ip"] or "—")}</code>',
                H.esc(ago(device["last_seen"])),
                actions + " " + _edit_device_form(device, teams),
            ])

        unknown = db.list_unknown()
        tried_web = sum(1 for e in unknown if e["source"] == "http")
        unknown_rows = []
        for entry in unknown:
            unknown_rows.append([
                f'<code>{H.esc(entry["mac"])}</code>',
                f'<code>{H.esc(entry["last_ip"] or "—")}</code>',
                H.esc("web" if entry["source"] == "http" else "barrido"),
                H.esc(entry["attempts"]),
                H.esc(ago(entry["last_seen"])),
                _add_device_form(teams, entry["mac"]),
            ])

        body = (
            H.alert(request.get("msg"), "info")
            + "<h1>Dispositivos</h1>"
            + '<p class="sub">Whitelist de MAC. Un equipo puede tener varios '
              "dispositivos. Solo el administrador ve esta información.</p>"
            + H.card("Dispositivos registrados", H.table(
                ["Estado", "MAC", "Etiqueta", "Equipo", "IP", "Visto", "Acciones"],
                rows, "No hay dispositivos dados de alta."))
            + H.card("Alta manual", _add_device_form(teams, "", wide=True))
            + H.card(f"MAC desconocidas ({len(unknown_rows)}, "
                     f"{tried_web} intentaron entrar a la web)",
                     '<p class="muted">En una red compartida aparecerán también '
                     "dispositivos ajenos al evento detectados por el barrido. "
                     "Los que han intentado abrir la web salen primero.</p>"
                     + H.table(
                         ["MAC", "IP", "Origen", "Intentos web", "Visto", "Dar de alta"],
                         unknown_rows,
                         "No se ha detectado ningún dispositivo fuera de la whitelist."))
        )
        return html(H.page("Dispositivos", body, client, "/admin/devices", admin=True,
                           extra_head=_admin_live()))

    @router.post("/admin/devices/add")
    def add_device(request):
        if (block := guard(request)):
            return block
        mac = arp.normalize_mac(request.get("mac"))
        if not mac:
            return redirect("/admin/devices", "MAC inválida. Formato: aa:bb:cc:dd:ee:ff")
        team_raw = request.get("team_id")
        team_id = int(team_raw) if team_raw.isdigit() else None
        db.add_device(mac, team_id, request.get("label"),
                      is_admin=request.get("is_admin") == "1")
        return redirect("/admin/devices", f"Dispositivo {mac} guardado.")

    @router.post("/admin/devices/delete")
    def delete_device(request):
        if (block := guard(request)):
            return block
        mac = arp.normalize_mac(request.get("mac"))
        if mac:
            db.delete_device(mac)
        return redirect("/admin/devices", "Dispositivo eliminado.")

    @router.post("/admin/devices/ignore")
    def ignore_unknown(request):
        if (block := guard(request)):
            return block
        db.delete_unknown(request.get("mac"))
        return redirect("/admin/devices", "Descartado de la lista.")

    # ── Historial de conexiones ───────────────────────────────────────────────

    @router.get("/admin/events")
    def event_history(request):
        if (block := guard(request)):
            return block
        client = request.client
        filter_event = request.get("event")
        filter_team = request.get("team_id")
        team_id = int(filter_team) if filter_team.isdigit() else None

        entries = db.list_device_events(
            limit=500,
            event=filter_event if filter_event in ("connect", "disconnect") else None,
            team_id=team_id,
        )
        db.mark_events_seen()

        rows = [[
            H.esc(fmt_local(e["ts"])),
            (H.badge("conexión", "ok") if e["event"] == "connect"
             else H.badge("desconexión", "err")),
            H.esc(e["team_name"] or "—"),
            H.esc(e["label"] or "—"),
            f'<code>{H.esc(e["mac"])}</code>',
            f'<code>{H.esc(e["ip"] or "—")}</code>',
            H.esc(ago(e["ts"])),
        ] for e in entries]

        options = ['<option value="">Todos los equipos</option>']
        for team in db.list_teams():
            selected = " selected" if team_id == team["id"] else ""
            options.append(
                f'<option value="{team["id"]}"{selected}>{H.esc(team["name"])}</option>')

        kinds = "".join(
            f'<option value="{value}"{" selected" if filter_event == value else ""}>'
            f"{label}</option>"
            for value, label in [("", "Todos los eventos"), ("connect", "Conexiones"),
                                 ("disconnect", "Desconexiones")]
        )

        filters = (
            '<form method="get" action="/admin/events" class="row">'
            f'<div><label>Equipo</label><select name="team_id">{"".join(options)}</select></div>'
            f'<div><label>Tipo</label><select name="event">{kinds}</select></div>'
            "<div><button class=\"small\">Filtrar</button></div></form>"
        )

        body = (
            "<h1>Conexiones y desconexiones</h1>"
            + '<p class="sub">Historial completo. Se registra cada vez que un '
              "dispositivo de la whitelist entra o sale de la red.</p>"
            + H.card("", filters)
            + H.card(f"{len(rows)} eventos", H.table(
                ["Fecha", "Evento", "Equipo", "Dispositivo", "MAC", "IP", "Hace"],
                rows, "Sin eventos registrados todavía."))
        )
        return html(H.page("Conexiones", body, client, "/admin/events", admin=True,
                           extra_head=_admin_live()))

    # ── Equipos ───────────────────────────────────────────────────────────────

    @router.get("/admin/teams")
    def teams(request):
        if (block := guard(request)):
            return block
        client = request.client
        rows = []
        for team in db.list_teams():
            rows.append([
                H.esc(team["name"]),
                f'{team["online_count"]}/{team["device_count"]}',
                H.esc(fmt_local(team["created_at"])),
                H.post_button("/admin/teams/delete", "Eliminar", {"team_id": team["id"]},
                              css="small danger",
                              confirm=f"¿Eliminar {team['name']} y todos sus runs?"),
            ])
        body = (
            H.alert(request.get("msg"), "info")
            + "<h1>Equipos</h1>"
            + H.card("Equipos registrados", H.table(
                ["Nombre", "Dispositivos conectados", "Alta", ""], rows,
                "No hay equipos."))
            + H.card("Nuevo equipo",
                     '<form method="post" action="/admin/teams/add" class="row">'
                     '<div><label>Nombre</label>'
                     '<input type="text" name="name" required></div>'
                     "<div><button>Crear</button></div></form>")
        )
        return html(H.page("Equipos", body, client, "/admin/teams", admin=True))

    @router.post("/admin/teams/add")
    def add_team(request):
        if (block := guard(request)):
            return block
        name = request.get("name").strip()
        if not name:
            return redirect("/admin/teams", "El nombre no puede estar vacío.")
        db.create_team(name)
        return redirect("/admin/teams", f"Equipo «{name}» creado.")

    @router.post("/admin/teams/delete")
    def delete_team(request):
        if (block := guard(request)):
            return block
        team_id = request.get("team_id")
        if team_id.isdigit():
            db.delete_team(int(team_id))
        return redirect("/admin/teams", "Equipo eliminado.")

    # ── Problemas ─────────────────────────────────────────────────────────────

    @router.get("/admin/problems")
    def problem_list(request):
        if (block := guard(request)):
            return block
        client = request.client
        rows = []
        for problem in problems.all_problems():
            missing = problem.missing_datasets()
            if missing:
                datasets = H.badge(f"faltan: {', '.join(missing)}", "err")
            elif problem.datasets:
                datasets = H.badge(f"{len(problem.datasets)} datasets", "ok")
            else:
                datasets = H.badge("sin datasets", "mute")
            rows.append([
                H.esc(problem.title) + f'<br><code class="muted">{H.esc(problem.slug)}</code>',
                H.badge("activo", "ok") if problem.enabled else H.badge("inactivo", "mute"),
                H.badge("abierto", "ok") if problem.is_open else H.badge("cerrado", "warn"),
                datasets,
                f'{problem.limits["cooldown_mins"]:g} min',
                H.post_button("/admin/problems/toggle",
                              "Desactivar" if problem.enabled else "Activar",
                              {"slug": problem.slug, "field": "enabled",
                               "value": "0" if problem.enabled else "1"})
                + " "
                + H.post_button("/admin/problems/toggle",
                                "Cerrar" if problem.is_open else "Abrir",
                                {"slug": problem.slug, "field": "is_open",
                                 "value": "0" if problem.is_open else "1"}),
            ])

        errors = problems.load_errors()
        error_block = ""
        if errors:
            error_block = H.card("No cargados", H.table(
                ["Carpeta", "Error"], [[H.esc(k), H.esc(v)] for k, v in errors.items()]))

        body = (
            H.alert(request.get("msg"), "info")
            + "<h1>Problemas</h1>"
            + '<p class="sub">Cada carpeta de <code>problems/</code> con un '
              "<code>manifest.toml</code> aparece aquí. «Activo» lo hace visible; "
              "«abierto» permite entregar y ejecutar.</p>"
            + H.card("", H.table(
                ["Problema", "Estado", "Entregas", "Datasets", "Espera", "Acciones"],
                rows, "No hay problemas en problems/."))
            + error_block
            + H.card("Recargar",
                     "<p class=\"muted\">Vuelve a leer las carpetas de problems/ sin "
                     "reiniciar el servidor. Los runs en curso no se ven afectados.</p>"
                     + H.post_button("/admin/problems/reload", "Recargar problemas",
                                     css="small"))
        )
        return html(H.page("Problemas", body, client, "/admin/problems", admin=True))

    @router.post("/admin/problems/toggle")
    def toggle_problem(request):
        if (block := guard(request)):
            return block
        slug, field = request.get("slug"), request.get("field")
        value = request.get("value") == "1"
        if field == "enabled":
            db.set_problem_flags(slug, enabled=value)
        elif field == "is_open":
            db.set_problem_flags(slug, is_open=value)
        problems.refresh_state(db)
        return redirect("/admin/problems", "Estado actualizado.")

    @router.post("/admin/problems/reload")
    def reload_problems(request):
        if (block := guard(request)):
            return block
        problems.load_all(db)
        count = len(problems.all_problems())
        return redirect("/admin/problems", f"{count} problema(s) cargados.")

    # ── Cola ──────────────────────────────────────────────────────────────────

    @router.get("/admin/queue")
    def queue_page(request):
        if (block := guard(request)):
            return block
        client = request.client

        cooldown_rows = []
        for team in db.list_teams():
            for problem in problems.active_problems():
                mins = db.minutes_since_last_run(team["id"], problem.slug)
                cooldown = problem.limits["cooldown_mins"]
                if mins is None or mins >= cooldown:
                    continue
                cooldown_rows.append([
                    H.esc(team["name"]),
                    H.esc(problem.title),
                    f"{cooldown - mins:.1f} min",
                    H.post_button("/admin/queue/reset-cooldown", "Resetear",
                                  {"team_id": team["id"], "slug": problem.slug}),
                ])

        recent = []
        for run in db.queue_snapshot():
            recent.append([
                f'<a href="/run/{run["id"]}">#{run["id"]}</a>',
                H.esc(run["team_name"]),
                H.esc(run["problem_slug"]),
                H.status_badge(run["status"]),
                H.esc(PHASE_LABELS.get(run["phase"], run["phase"])),
                H.esc(ago(run["started_at"] or run["created_at"])),
                (H.post_button("/admin/queue/cancel", "Cancelar", {"run_id": run["id"]},
                               css="small danger")
                 if run["status"] == "queued" else ""),
            ])

        body = (
            H.alert(request.get("msg"), "info")
            + "<h1>Cola de evaluación</h1>"
            + f'<p class="sub">{runq.workers} evaluaciones en paralelo como máximo. '
              "La cola se conserva si el servidor se reinicia.</p>"
            + H.card("En curso y en cola", H.table(
                ["Run", "Equipo", "Problema", "Estado", "Fase", "Desde", ""],
                recent, "La cola está vacía."))
            + H.card("Esperas activas (cooldown)", H.table(
                ["Equipo", "Problema", "Restante", ""], cooldown_rows,
                "Ningún equipo está esperando."))
        )
        return html(H.page("Cola", body, client, "/admin/queue", admin=True,
                           extra_head=_admin_live()))

    @router.post("/admin/queue/cancel")
    def cancel_run(request):
        if (block := guard(request)):
            return block
        run_id = request.get("run_id")
        if run_id.isdigit() and db.cancel_run(int(run_id)):
            return redirect("/admin/queue", f"Run #{run_id} cancelado.")
        return redirect("/admin/queue", "Solo se pueden cancelar runs en cola.")

    @router.post("/admin/queue/reset-cooldown")
    def reset_cooldown(request):
        if (block := guard(request)):
            return block
        team_id = request.get("team_id")
        if team_id.isdigit():
            db.reset_cooldown(int(team_id), request.get("slug"))
        return redirect("/admin/queue", "Espera reseteada.")

    # ── SSE de admin (incluye eventos de red) ─────────────────────────────────

    @router.get("/admin/stream")
    def admin_stream(request):
        if not request.client.is_admin:
            return Response("", 403, "text/plain")
        return _sse("admin")


# ── Fragmentos ────────────────────────────────────────────────────────────────


def _events_feed(entries: list[dict]) -> str:
    if not entries:
        return '<p class="muted">Sin eventos todavía.</p>'
    items = []
    for e in entries:
        kind = "ok" if e["event"] == "connect" else "err"
        verb = "se conectó" if e["event"] == "connect" else "se desconectó"
        items.append(
            f'<div class="ev"><time>{H.esc(fmt_local(e["ts"]))}</time>'
            f'{H.badge(verb, kind)} '
            f'<span><strong>{H.esc(e["label"] or e["mac"])}</strong> '
            f'<span class="muted">· {H.esc(e["team_name"] or "sin equipo")}</span></span></div>'
        )
    return f'<div class="feed">{"".join(items)}</div>'


def _queue_rows(db) -> list[list[str]]:
    rows = []
    for run in db.queue_snapshot():
        rows.append([
            f'<a href="/run/{run["id"]}">#{run["id"]}</a>',
            H.esc(run["team_name"]),
            H.esc(run["problem_slug"]),
            H.status_badge(run["status"]),
            H.esc(PHASE_LABELS.get(run["phase"], run["phase"])),
            H.esc(ago(run["started_at"] or run["created_at"])),
        ])
    return rows


def _team_options(teams: list[dict], selected: int | None = None) -> str:
    options = ['<option value="">— sin equipo —</option>']
    for team in teams:
        mark = " selected" if selected == team["id"] else ""
        options.append(f'<option value="{team["id"]}"{mark}>{H.esc(team["name"])}</option>')
    return "".join(options)


def _add_device_form(teams: list[dict], mac: str, wide: bool = False) -> str:
    mac_field = (f'<div><label>MAC</label><input type="text" name="mac" '
                 f'placeholder="aa:bb:cc:dd:ee:ff" required></div>' if wide
                 else f'<input type="hidden" name="mac" value="{H.esc(mac)}">')
    label_field = ('<div><label>Etiqueta</label>'
                   '<input type="text" name="label" placeholder="Portátil de Ana"></div>'
                   if wide else
                   '<input type="hidden" name="label" value="">')
    admin_field = ('<div><label>&nbsp;</label><label class="muted">'
                   '<input type="checkbox" name="is_admin" value="1">admin</label></div>'
                   if wide else "")
    return (
        '<form method="post" action="/admin/devices/add" class="row">'
        + mac_field + label_field
        + f'<div><label>Equipo</label><select name="team_id">{_team_options(teams)}</select></div>'
        + admin_field
        + f'<div><button class="small">{"Añadir" if wide else "Dar de alta"}</button></div>'
        + "</form>"
    )


def _edit_device_form(device: dict, teams: list[dict]) -> str:
    return (
        '<form method="post" action="/admin/devices/add" class="inline">'
        f'<input type="hidden" name="mac" value="{H.esc(device["mac"])}">'
        f'<input type="hidden" name="label" value="{H.esc(device["label"])}">'
        f'<input type="hidden" name="is_admin" value="{1 if device["is_admin"] else 0}">'
        f'<select name="team_id" style="width:auto;padding:4px 8px" '
        f'onchange="this.form.submit()">{_team_options(teams, device["team_id"])}</select>'
        "</form>"
    )


def _admin_live() -> str:
    """Refresca el panel cuando entra un evento de red o cambia un run."""
    return """<script>
(function () {
  if (!window.EventSource) return;
  var es = new EventSource('/admin/stream');
  var pending = null;
  var refresh = function () {
    clearTimeout(pending);
    pending = setTimeout(function () { location.reload(); }, 1200);
  };
  // Solo los cambios reales recargan: el barrido periódico no debe hacerlo.
  ['device_event', 'run_update', 'run_queued'].forEach(function (k) {
    es.addEventListener(k, refresh);
  });
  es.addEventListener('device_event', function (ev) {
    try {
      var d = JSON.parse(ev.data);
      var title = d.event === 'connect' ? 'Dispositivo conectado' : 'Dispositivo desconectado';
      var name = (d.label || d.mac) + ' — ' + (d.team_name || 'sin equipo');
      if (window.Notification && Notification.permission === 'granted') {
        new Notification(title, { body: name });
      }
      document.title = '● ' + document.title.replace(/^● /, '');
    } catch (e) {}
  });
  if (window.Notification && Notification.permission === 'default') {
    document.addEventListener('click', function once() {
      Notification.requestPermission();
      document.removeEventListener('click', once);
    });
  }
})();
</script>"""
