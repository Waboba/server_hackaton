"""
routes_team.py — Páginas de los equipos.

Reemplaza los comandos del bot de Telegram:
    /submit      → formulario en /p/<problema>
    /run         → botón «Ejecutar evaluación»
    /status      → /runs y la página de cada run
    /leaderboard → /leaderboard

Ningún endpoint de este módulo expone información de red: los dispositivos,
las conexiones y el historial son exclusivos del panel de administración.
"""

import json
import logging
import queue
import shutil
import time
from pathlib import Path

from app import events, problems
from app.config import SUBMISSIONS_DIR
from app.db import ago, fmt_local
from app.queue import PHASE_LABELS
from app.validator import validate_submission
from app.web import html as H
from app.web.http import Response, StreamResponse, attachment, html, redirect

log = logging.getLogger(__name__)

KEEP_SUBMISSIONS = 5  # copias por equipo y problema


def register(router, ctx):
    db, runq = ctx.db, ctx.runq

    # ── Estáticos ─────────────────────────────────────────────────────────────

    @router.get("/static/style.css")
    def style(request):
        return Response(H.CSS, 200, "text/css; charset=utf-8",
                        [("Cache-Control", "max-age=3600")])

    # ── Inicio ────────────────────────────────────────────────────────────────

    @router.get("/")
    def home(request):
        client = request.client
        active = problems.active_problems()
        message = request.get("msg")

        if not active:
            body = (H.alert(message, "info")
                    + "<h1>Aún no hay problemas activos</h1>"
                    + '<p class="sub">El organizador todavía no ha abierto ningún '
                      "problema. Vuelve a cargar la página más tarde.</p>")
            return html(H.page("Inicio", body, client, "/"))

        cards = []
        for problem in active:
            last = db.last_run(client.team_id, problem.slug)
            submission = db.latest_submission(client.team_id, problem.slug)
            ready = bool(submission and not submission["consumed"])

            if last:
                state = H.status_badge(last["status"])
                if last["status"] == "done":
                    state += f' · score <span class="score">{last["score"]:.4f}</span>'
                elif last["status"] == "running":
                    state += f' · {H.esc(PHASE_LABELS.get(last["phase"], last["phase"]))}'
                when = f' <span class="muted">({ago(last["finished_at"] or last["created_at"])})</span>'
            else:
                state, when = '<span class="muted">sin evaluaciones</span>', ""

            entrega = ("entrega lista para ejecutar" if ready
                       else ("hay que volver a entregar antes de ejecutar"
                             if submission else "sin entrega"))
            status = "" if problem.is_open else H.badge("cerrado", "warn")

            cards.append(H.card(
                problem.title,
                f'<p class="muted">{H.esc(problem.description)}</p>'
                f"<p>{state}{when}</p>"
                f'<p class="muted">{H.esc(entrega)}</p>'
                f'<p><a class="btn small" href="/p/{H.esc(problem.slug)}">Abrir</a> '
                f'<a class="btn small secondary" href="/p/{H.esc(problem.slug)}/leaderboard">'
                f"Leaderboard</a> {status}</p>"
            ))

        body = (H.alert(message, "info")
                + f"<h1>Hola, {H.esc(client.team_name)}</h1>"
                + '<p class="sub">Elige un problema para entregar tu solución y '
                  "lanzar la evaluación.</p>"
                + f'<div class="grid">{"".join(cards)}</div>')
        return html(H.page("Inicio", body, client, "/"))

    # ── Página de un problema ─────────────────────────────────────────────────

    @router.get("/p/{slug}")
    def problem_page(request):
        client = request.client
        problem = _require_problem(request)
        if isinstance(problem, Response):
            return problem

        last = db.last_run(client.team_id, problem.slug)
        submission = db.latest_submission(client.team_id, problem.slug)
        ready = bool(submission and not submission["consumed"])
        limits = problem.limits

        # Formulario de entrega
        inputs = []
        for spec in problem.files:
            mark = "" if spec.get("required") else " (opcional)"
            note = spec.get("help", "")
            inputs.append(
                f'<label>{H.esc(spec["name"])}{mark}'
                + (f' — <span class="muted">{H.esc(note)}</span>' if note else "")
                + "</label>"
                + f'<input type="file" name="{H.esc(spec["name"])}"'
                + (" required" if spec.get("required") else "") + ">"
            )

        closed = not (problem.is_open and ctx.submissions_open())
        disabled = " disabled" if closed else ""
        submit_form = (
            f'<form method="post" action="/p/{H.esc(problem.slug)}/submit" '
            'enctype="multipart/form-data">'
            + "".join(inputs)
            + f'<p style="margin-top:16px"><button{disabled}>Entregar</button></p></form>'
        )

        can_run, reason = runq.can_run(client.team_id, problem)
        run_note = ""
        if closed:
            run_note = "Las entregas están cerradas."
        elif not ready:
            run_note = "Debes entregar tu solución antes de cada ejecución."
        elif not can_run:
            run_note = reason
        run_disabled = " disabled" if (closed or not ready or not can_run) else ""
        run_form = (
            f'<form method="post" action="/p/{H.esc(problem.slug)}/run">'
            f"<button{run_disabled}>Ejecutar evaluación</button></form>"
            + (f'<p class="muted" style="margin-top:10px">{H.esc(run_note)}</p>'
               if run_note else "")
        )

        # Estado del último run
        if last:
            rows = [
                ["Estado", H.status_badge(last["status"])],
                ["Enviado", H.esc(fmt_local(last["created_at"]))],
            ]
            if last["status"] in ("queued", "running"):
                phase = PHASE_LABELS.get(last["phase"], last["phase"])
                if last["status"] == "queued":
                    phase = f"En cola (posición {db.queue_position(last['id'])})"
                rows.append(["Fase", H.esc(phase)])
            if last["status"] == "done":
                rows.append(["Score", f'<span class="score">{last["score"]:.4f}</span>'])
                rows.append(["Terminado", H.esc(fmt_local(last["finished_at"]))])
            if last["status"] == "error":
                rows.append(["Error", f'<code>{H.esc((last["error_msg"] or "")[:400])}</code>'])
            rows.append(["Detalle", f'<a href="/run/{last["id"]}">ver resultado completo</a>'])
            last_block = H.table(["", ""], rows)
        else:
            last_block = '<p class="muted">Todavía no has ejecutado ninguna evaluación.</p>'

        # Plantillas descargables
        template_links = ""
        if problem.template_dir:
            links = [
                f'<a href="/p/{H.esc(problem.slug)}/template/{H.esc(f.name)}">{H.esc(f.name)}</a>'
                for f in sorted(problem.template_dir.iterdir()) if f.is_file()
            ]
            if links:
                template_links = "<p>Plantillas: " + " · ".join(links) + "</p>"

        specs = H.table(
            ["Límite", "Valor"],
            [
                ["Tiempo máximo de ejecución", f'{limits["timeout_secs"]} s'],
                ["Memoria", H.esc(limits["memory"])],
                ["CPUs", H.esc(limits["cpus"])],
                ["Espera entre ejecuciones", f'{limits["cooldown_mins"]:g} min'],
                ["Paquetes extra permitidos",
                 H.esc(problem.submission.get("max_packages", 10))],
                ["Red dentro del contenedor", "sin acceso"],
            ],
        )

        statement = problem.manifest.get("statement", "")
        statement_block = (H.card("Enunciado", f"<pre>{H.esc(statement)}</pre>")
                           if statement else "")

        body = (
            H.alert(request.get("msg"), request.get("kind") or "info")
            + f"<h1>{H.esc(problem.title)}</h1>"
            + f'<p class="sub">{H.esc(problem.description)}</p>'
            + statement_block
            + '<div class="grid">'
            + H.card("Entregar solución", submit_form + template_links)
            + H.card("Ejecutar", run_form)
            + "</div>"
            + H.card("Tu último run", last_block)
            + H.card("Condiciones de ejecución", specs)
            + f'<p><a href="/p/{H.esc(problem.slug)}/leaderboard">Ver leaderboard '
              "de este problema</a></p>"
        )
        return html(H.page(problem.title, body, client, "/",
                           extra_head=_live_script()))

    # ── Entregar ──────────────────────────────────────────────────────────────

    @router.post("/p/{slug}/submit")
    def submit(request):
        client = request.client
        problem = _require_problem(request)
        if isinstance(problem, Response):
            return problem

        base = f"/p/{problem.slug}"
        if not ctx.submissions_open():
            return redirect(base, "Las entregas están cerradas.")
        if not problem.is_open:
            return redirect(base, "Este problema está cerrado.")

        target = (SUBMISSIONS_DIR / problem.slug / f"team_{client.team_id}"
                  / f"{int(time.time())}_{request.client_ip.replace('.', '-')}")
        target.mkdir(parents=True, exist_ok=True)

        saved = []
        for spec in problem.files:
            name = spec["name"]
            uploaded = request.files.get(name)
            if not uploaded:
                if spec.get("required"):
                    shutil.rmtree(target, ignore_errors=True)
                    return redirect(base, f"Falta el archivo {name}.")
                continue
            _, content = uploaded
            # Se guarda con el nombre declarado en el manifest, nunca con el del
            # archivo subido: elimina cualquier riesgo de path traversal.
            (target / name).write_bytes(content)
            saved.append(name)

        if not saved:
            shutil.rmtree(target, ignore_errors=True)
            return redirect(base, "No se recibió ningún archivo.")

        ok, error = validate_submission(problem, target)
        if not ok:
            shutil.rmtree(target, ignore_errors=True)
            return redirect(base, f"Entrega rechazada: {error}")

        submission_id = db.add_submission(client.team_id, problem.slug,
                                          str(target), client.mac)
        _prune_submissions(target.parent, keep=KEEP_SUBMISSIONS)
        events.publish_team(client.team_id, "submission", {
            "problem": problem.slug, "submission_id": submission_id,
        })
        log.info("Entrega de %s para %s (%s)", client.team_name, problem.slug,
                 ", ".join(saved))
        return redirect(base, "Entrega guardada y validada. Ya puedes ejecutar.")

    # ── Ejecutar ──────────────────────────────────────────────────────────────

    @router.post("/p/{slug}/run")
    def run(request):
        client = request.client
        problem = _require_problem(request)
        if isinstance(problem, Response):
            return problem

        base = f"/p/{problem.slug}"
        if not ctx.submissions_open():
            return redirect(base, "Las entregas están cerradas.")
        if not problem.is_open:
            return redirect(base, "Este problema está cerrado.")

        submission = db.latest_submission(client.team_id, problem.slug)
        if not submission:
            return redirect(base, "No has entregado ninguna solución todavía.")
        if submission["consumed"]:
            return redirect(base, "Debes volver a entregar tu solución antes de "
                                  "cada ejecución.")

        allowed, reason = runq.can_run(client.team_id, problem)
        if not allowed:
            return redirect(base, reason)

        run_id = runq.enqueue(client.team_id, problem.slug, submission["id"])
        position = db.queue_position(run_id)
        note = ("Evaluación encolada." if position <= ctx.runq.workers
                else f"Evaluación en cola (posición {position}).")
        return redirect(f"/run/{run_id}", note)

    # ── Runs ──────────────────────────────────────────────────────────────────

    @router.get("/runs")
    def my_runs(request):
        client = request.client
        rows = []
        for run in db.team_runs(client.team_id, limit=40):
            problem = problems.get(run["problem_slug"])
            score = f'{run["score"]:.4f}' if run["score"] is not None else "—"
            rows.append([
                f'<a href="/run/{run["id"]}">#{run["id"]}</a>',
                H.esc(problem.title if problem else run["problem_slug"]),
                H.status_badge(run["status"]),
                f'<span class="score">{score}</span>',
                H.esc(fmt_local(run["created_at"])),
                H.esc(ago(run["finished_at"] or run["created_at"])),
            ])
        body = (
            "<h1>Mis evaluaciones</h1>"
            + '<p class="sub">Historial de las ejecuciones de tu equipo.</p>'
            + H.table(["Run", "Problema", "Estado", "Score", "Enviado", "Actualizado"],
                      rows, "Todavía no has ejecutado ninguna evaluación.")
        )
        return html(H.page("Mis evaluaciones", body, client, "/runs"))

    @router.get("/run/{id}")
    def run_detail(request):
        client = request.client
        try:
            run_id = int(request.params["id"])
        except ValueError:
            return html(H.simple_page("404", "Run no encontrado.", client), 404)

        run = db.get_run(run_id)
        if not run:
            return html(H.simple_page("404", "Run no encontrado.", client), 404)
        if run["team_id"] != client.team_id and not client.is_admin:
            return html(H.simple_page("403", "Este run es de otro equipo.", client), 403)

        problem = problems.get(run["problem_slug"])
        rows = [
            ["Problema", H.esc(problem.title if problem else run["problem_slug"])],
            ["Equipo", H.esc(run["team_name"])],
            ["Estado", H.status_badge(run["status"])],
            ["Enviado", H.esc(fmt_local(run["created_at"]))],
        ]
        if run["status"] in ("queued", "running"):
            phase = PHASE_LABELS.get(run["phase"], run["phase"])
            if run["status"] == "queued":
                phase = f"En cola (posición {db.queue_position(run_id)})"
            rows.append(["Fase", H.esc(phase)])
        if run["finished_at"]:
            rows.append(["Terminado", H.esc(fmt_local(run["finished_at"]))])
        if run["score"] is not None:
            rows.append(["Score", f'<span class="score">{run["score"]:.4f}</span>'])

        blocks = ""
        if run.get("result") and problem:
            summary = problem.summary(run["result"])
            if summary:
                blocks += H.card("Resumen", H.table(
                    ["Métrica", "Valor"],
                    [[H.esc(k), H.esc(v)] for k, v in summary.items()],
                ))
            for title, content in problem.detail_blocks(run["result"]):
                blocks += H.card(title, f"<pre>{H.esc(content)}</pre>")

        if run["status"] == "error":
            blocks += H.card("Error", f'<pre>{H.esc(run["error_msg"] or "")}</pre>')

        if client.is_admin and run.get("log"):
            blocks += H.card("Log de ejecución (admin)", f'<pre>{H.esc(run["log"])}</pre>')

        auto = _live_script() if run["status"] in ("queued", "running") else ""
        body = (
            H.alert(request.get("msg"), "info")
            + f"<h1>Run #{run_id}</h1>"
            + H.card("", H.table(["", ""], rows))
            + blocks
        )
        return html(H.page(f"Run #{run_id}", body, client, "/runs", extra_head=auto))

    # ── Leaderboards ──────────────────────────────────────────────────────────

    @router.get("/leaderboard")
    def leaderboard_all(request):
        client = request.client
        sections = []
        for problem in problems.active_problems():
            sections.append(f"<h2>{H.esc(problem.title)}</h2>"
                            + _leaderboard_table(problem, db))
        if not sections:
            sections = ['<p class="muted">No hay problemas activos.</p>']
        body = ("<h1>Leaderboard</h1>"
                + '<p class="sub">Ranking por problema. El score es público.</p>'
                + "".join(sections))
        return html(H.page("Leaderboard", body, client, "/leaderboard"))

    @router.get("/p/{slug}/leaderboard")
    def leaderboard_one(request):
        client = request.client
        problem = _require_problem(request, require_open=False)
        if isinstance(problem, Response):
            return problem
        body = (f"<h1>Leaderboard · {H.esc(problem.title)}</h1>"
                + f'<p class="sub">Criterio: '
                  f'{"mejor" if problem.ranking == "best" else "último"} run completado '
                  "de cada equipo.</p>"
                + _leaderboard_table(problem, db)
                + f'<p><a href="/p/{H.esc(problem.slug)}">Volver al problema</a></p>')
        return html(H.page(f"Leaderboard {problem.title}", body, client, "/leaderboard"))

    # ── Plantillas ────────────────────────────────────────────────────────────

    @router.get("/p/{slug}/template/{name}")
    def template_file(request):
        problem = _require_problem(request, require_open=False)
        if isinstance(problem, Response):
            return problem
        if not problem.template_dir:
            return html(H.simple_page("404", "Sin plantillas.", request.client), 404)
        name = Path(request.params["name"]).name  # sin rutas
        path = problem.template_dir / name
        if not path.is_file():
            return html(H.simple_page("404", "Archivo no encontrado.", request.client), 404)
        return attachment(path.read_bytes(), name)

    # ── SSE del equipo (sin datos de red) ─────────────────────────────────────

    @router.get("/events")
    def team_stream(request):
        return _sse(f"team:{request.client.team_id}")

    # ── Utilidades locales ────────────────────────────────────────────────────

    def _require_problem(request, require_open: bool = True):
        slug = request.params.get("slug", "")
        problem = problems.get(slug)
        if problem is None or not problem.enabled:
            return html(H.simple_page(
                "404", "Ese problema no existe o no está activo.", request.client), 404)
        return problem


def _leaderboard_table(problem, db) -> str:
    entries = db.leaderboard_runs(problem.slug, problem.ranking)
    if not entries:
        return '<p class="muted">Todavía no hay resultados.</p>'

    columns = problem.summary_columns()
    if not columns:
        columns = list(problem.summary(entries[0]["result"]).keys())

    rows = []
    for i, entry in enumerate(entries, start=1):
        summary = problem.summary(entry["result"])
        cells = [
            f'<span class="rank">{i}</span>',
            H.esc(entry["team_name"]),
            f'<span class="score">{(entry["score"] or 0):.4f}</span>',
        ]
        cells += [H.esc(summary.get(col, "—")) for col in columns]
        cells.append(H.esc(ago(entry["finished_at"])))
        rows.append(cells)

    return H.table(["#", "Equipo", "Score"] + columns + ["Actualizado"], rows)


def _prune_submissions(team_dir: Path, keep: int):
    """Conserva solo las últimas `keep` entregas de un equipo en un problema."""
    try:
        dirs = sorted((d for d in team_dir.iterdir() if d.is_dir()),
                      key=lambda d: d.stat().st_mtime, reverse=True)
        for old in dirs[keep:]:
            shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass


def _live_script() -> str:
    """Recarga la página cuando llega un evento del propio equipo."""
    return """<script>
(function () {
  if (!window.EventSource) return;
  var es = new EventSource('/events');
  var reload = function () { setTimeout(function () { location.reload(); }, 400); };
  es.addEventListener('run_update', reload);
  es.addEventListener('run_queued', reload);
})();
</script>"""


def _sse(channel: str) -> StreamResponse:
    """Stream SSE genérico sobre el bus de eventos."""
    def generate():
        subscription = events.subscribe(channel)
        try:
            yield b": conectado\n\n"
            while True:
                try:
                    message = subscription.get(timeout=20)
                except queue.Empty:
                    yield b": ping\n\n"     # mantiene viva la conexión
                    continue
                payload = json.loads(message)
                data = json.dumps(payload["data"])
                yield f"event: {payload['kind']}\ndata: {data}\n\n".encode()
        finally:
            events.unsubscribe(subscription)

    return StreamResponse(generate())
