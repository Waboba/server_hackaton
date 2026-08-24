"""
html.py — Construcción de las páginas.

Sin motor de plantillas para mantener el proyecto sin dependencias: las páginas
son tablas y formularios, y estos ayudantes bastan. Todo el texto dinámico pasa
por esc().
"""

from html import escape

from app.config import EVENT_TITLE

CSS = """
:root {
  --bg: #0f1115; --panel: #171a21; --panel-2: #1e222b; --border: #2a2f3a;
  --fg: #e6e8ec; --muted: #9aa1ae; --accent: #4f9cf9; --accent-fg: #06131f;
  --ok: #43c47a; --warn: #e8b84b; --err: #ef5f5f; --mono: ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
       font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
header { background: var(--panel); border-bottom: 1px solid var(--border);
         padding: 0 20px; position: sticky; top: 0; z-index: 10; }
.headrow { display: flex; align-items: center; gap: 18px; max-width: 1180px;
           margin: 0 auto; min-height: 56px; flex-wrap: wrap; }
.brand { font-weight: 700; letter-spacing: .2px; }
nav { display: flex; gap: 14px; flex-wrap: wrap; }
nav a { color: var(--muted); padding: 6px 0; font-size: 14px; }
nav a.active, nav a:hover { color: var(--fg); text-decoration: none; }
.spacer { flex: 1; }
.whoami { font-size: 13px; color: var(--muted); text-align: right; }
main { max-width: 1180px; margin: 24px auto 60px; padding: 0 20px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 26px 0 10px; }
.sub { color: var(--muted); font-size: 14px; margin: 0 0 20px; }
.card { background: var(--panel); border: 1px solid var(--border);
        border-radius: 10px; padding: 18px 20px; margin-bottom: 18px; }
.card h3 { margin: 0 0 12px; font-size: 15px; letter-spacing: .3px; }
.grid { display: grid; gap: 18px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--border); }
th { color: var(--muted); font-weight: 600; font-size: 12px;
     text-transform: uppercase; letter-spacing: .5px; }
tr:last-child td { border-bottom: none; }
tbody tr:hover { background: var(--panel-2); }
.table-wrap { overflow-x: auto; }
.mono, code, pre { font-family: var(--mono); font-size: 13px; }
pre { background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px;
      padding: 12px 14px; overflow-x: auto; margin: 8px 0; white-space: pre; }
.badge { display: inline-block; padding: 2px 9px; border-radius: 999px;
         font-size: 12px; font-weight: 600; border: 1px solid transparent; }
.badge.ok   { background: rgba(67,196,122,.15);  color: var(--ok);   border-color: rgba(67,196,122,.3); }
.badge.warn { background: rgba(232,184,75,.15);  color: var(--warn); border-color: rgba(232,184,75,.3); }
.badge.err  { background: rgba(239,95,95,.15);   color: var(--err);  border-color: rgba(239,95,95,.3); }
.badge.mute { background: var(--panel-2); color: var(--muted); border-color: var(--border); }
.alert { padding: 11px 14px; border-radius: 8px; margin-bottom: 16px; font-size: 14px;
         border: 1px solid; }
.alert.ok   { background: rgba(67,196,122,.1);  border-color: rgba(67,196,122,.35); }
.alert.err  { background: rgba(239,95,95,.1);   border-color: rgba(239,95,95,.35); }
.alert.warn { background: rgba(232,184,75,.1);  border-color: rgba(232,184,75,.35); }
.alert.info { background: var(--panel-2); border-color: var(--border); }
form.inline { display: inline; }
label { display: block; font-size: 13px; color: var(--muted); margin: 12px 0 5px; }
input[type=text], input[type=password], input[type=file], select, textarea {
  width: 100%; max-width: 420px; background: var(--panel-2); color: var(--fg);
  border: 1px solid var(--border); border-radius: 7px; padding: 9px 11px;
  font: inherit; }
input[type=checkbox] { margin-right: 6px; }
button, .btn { background: var(--accent); color: var(--accent-fg); border: none;
  border-radius: 7px; padding: 9px 16px; font: inherit; font-weight: 600;
  cursor: pointer; display: inline-block; }
button:hover, .btn:hover { filter: brightness(1.1); text-decoration: none; }
button:disabled { opacity: .45; cursor: not-allowed; }
button.secondary, .btn.secondary { background: var(--panel-2); color: var(--fg);
  border: 1px solid var(--border); }
button.danger { background: rgba(239,95,95,.15); color: var(--err);
  border: 1px solid rgba(239,95,95,.4); }
button.small, .btn.small { padding: 5px 11px; font-size: 13px; }
.row { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
.stat { background: var(--panel-2); border: 1px solid var(--border);
        border-radius: 9px; padding: 14px 16px; }
.stat .n { font-size: 26px; font-weight: 700; line-height: 1.1; }
.stat .l { color: var(--muted); font-size: 12px; text-transform: uppercase;
           letter-spacing: .5px; margin-top: 3px; }
.rank { color: var(--muted); font-variant-numeric: tabular-nums; width: 34px; }
.score { font-weight: 700; font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); }
.feed { max-height: 420px; overflow-y: auto; }
.feed .ev { display: flex; gap: 10px; align-items: baseline; padding: 7px 0;
            border-bottom: 1px solid var(--border); font-size: 14px; }
.feed .ev time { color: var(--muted); font-size: 12px; font-family: var(--mono);
                 white-space: nowrap; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block;
       margin-right: 7px; vertical-align: middle; }
.dot.on { background: var(--ok); box-shadow: 0 0 0 3px rgba(67,196,122,.18); }
.dot.off { background: #555b68; }
footer { color: var(--muted); font-size: 12px; text-align: center; padding: 30px 0; }
@media (max-width: 640px) { .headrow { min-height: 0; padding: 10px 0; } }
"""

TEAM_NAV = [
    ("/", "Inicio"),
    ("/leaderboard", "Leaderboard"),
    ("/runs", "Mis evaluaciones"),
]

ADMIN_NAV = [
    ("/admin", "Resumen"),
    ("/admin/devices", "Dispositivos"),
    ("/admin/events", "Conexiones"),
    ("/admin/teams", "Equipos"),
    ("/admin/problems", "Problemas"),
    ("/admin/queue", "Cola"),
]


def esc(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def page(title: str, body: str, client=None, active: str = "",
         admin: bool = False, extra_head: str = "") -> str:
    nav_items = ADMIN_NAV if admin else TEAM_NAV
    nav = "".join(
        f'<a href="{esc(href)}" class="{"active" if href == active else ""}">{esc(label)}</a>'
        for href, label in nav_items
    )

    if client is not None and getattr(client, "known", False):
        who = f"{esc(client.team_name)} · {esc(client.device_label)}"
        if client.is_admin:
            who += ' · <span class="badge ok">admin</span>'
        elif client.is_admin_device:
            who += ' · <a href="/admin/login">entrar como admin</a>'
    else:
        who = ""

    switch = ('<a href="/">Vista de equipo</a>' if admin
              else ('<a href="/admin">Panel admin</a>'
                    if client is not None and getattr(client, "is_admin", False) else ""))

    return f"""<!doctype html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · {esc(EVENT_TITLE)}</title>
<link rel="stylesheet" href="/static/style.css">
{extra_head}
</head><body>
<header><div class="headrow">
  <span class="brand">{esc(EVENT_TITLE)}{' · admin' if admin else ''}</span>
  <nav>{nav}{switch}</nav>
  <span class="spacer"></span>
  <span class="whoami">{who}</span>
</div></header>
<main>{body}</main>
<footer>Servidor local del evento · sin conexión a internet requerida</footer>
</body></html>"""


def simple_page(title: str, message: str, client=None) -> str:
    body = f'<h1>{esc(title)}</h1><p class="sub">{esc(message)}</p>'
    return page(title, body, client)


# ── Componentes ───────────────────────────────────────────────────────────────


def alert(message: str, kind: str = "info") -> str:
    if not message:
        return ""
    return f'<div class="alert {kind}">{esc(message)}</div>'


def raw_alert(html_message: str, kind: str = "info") -> str:
    return f'<div class="alert {kind}">{html_message}</div>'


def card(title: str, body: str) -> str:
    heading = f"<h3>{esc(title)}</h3>" if title else ""
    return f'<div class="card">{heading}{body}</div>'


def table(headers: list[str], rows: list[list[str]], empty: str = "Sin datos.") -> str:
    """Las celdas se insertan como HTML: escápalas con esc() al construirlas."""
    if not rows:
        return f'<p class="muted">{esc(empty)}</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return (f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def badge(text: str, kind: str = "mute") -> str:
    return f'<span class="badge {kind}">{esc(text)}</span>'


def stat(number, label: str) -> str:
    return f'<div class="stat"><div class="n">{esc(number)}</div><div class="l">{esc(label)}</div></div>'


def status_badge(status: str) -> str:
    kinds = {"done": "ok", "running": "warn", "queued": "mute",
             "error": "err", "cancelled": "mute"}
    labels = {"done": "completado", "running": "ejecutando", "queued": "en cola",
              "error": "error", "cancelled": "cancelado"}
    return badge(labels.get(status, status), kinds.get(status, "mute"))


def online_dot(status: str) -> str:
    cls = "on" if status == "online" else "off"
    label = "conectado" if status == "online" else "desconectado"
    return f'<span class="dot {cls}"></span>{esc(label)}'


def post_button(action: str, label: str, fields: dict | None = None,
                css: str = "small secondary", confirm: str = "") -> str:
    hidden = "".join(
        f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">'
        for k, v in (fields or {}).items()
    )
    onclick = f' onclick="return confirm(\'{esc(confirm)}\')"' if confirm else ""
    return (f'<form method="post" action="{esc(action)}" class="inline">{hidden}'
            f'<button class="{css}"{onclick}>{esc(label)}</button></form>')
