"""
queue.py — Cola de evaluaciones persistida en la base de datos.

Se conserva el comportamiento del bot original (FIFO, N evaluaciones en
paralelo, cooldown por equipo) con dos mejoras:

  - La cola vive en la tabla `runs`, así que sobrevive a un reinicio del
    servidor en lugar de perderse con el proceso.
  - El cooldown y la cola son por (equipo, problema), no globales, porque
    ahora puede haber varios problemas activos a la vez.
"""

import logging
import threading
import time
from pathlib import Path

from app import events, problems
from app.config import MAX_CONCURRENT
from app.runner import RunnerError, run_submission

log = logging.getLogger(__name__)

PHASE_LABELS = {
    "queued": "En cola",
    "starting": "Preparando",
    "building": "Construyendo imagen Docker",
    "running": "Ejecutando contenedor",
    "scoring": "Calculando resultado",
    "done": "Completado",
    "error": "Error",
    "cancelled": "Cancelado",
}


class RunQueue:
    def __init__(self, db, workers: int = MAX_CONCURRENT):
        self.db = db
        self.workers = max(1, workers)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    def start(self):
        orphans = self.db.reset_orphan_runs()
        if orphans:
            log.warning("%d run(s) interrumpidos por un reinicio previo", orphans)
        for i in range(self.workers):
            t = threading.Thread(target=self._worker_loop, name=f"worker-{i+1}",
                                 daemon=True)
            t.start()
            self._threads.append(t)
        log.info("Cola arrancada con %d worker(s)", self.workers)
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    # ── API ───────────────────────────────────────────────────────────────────

    def enqueue(self, team_id: int, slug: str, submission_id: int) -> int:
        run_id = self.db.create_run(team_id, slug, submission_id)
        self.db.consume_submission(submission_id)
        position = self.db.queue_position(run_id)
        events.publish_team(team_id, "run_queued", {
            "run_id": run_id, "problem": slug, "position": position,
        })
        self._wake.set()
        return run_id

    def can_run(self, team_id: int, problem) -> tuple[bool, str]:
        """Comprueba cooldown y runs pendientes. (permitido, motivo)."""
        if self.db.has_pending_run(team_id, problem.slug):
            return False, "Ya tienes una evaluación en curso o en cola para este problema."
        cooldown = problem.limits["cooldown_mins"]
        if cooldown > 0:
            mins = self.db.minutes_since_last_run(team_id, problem.slug)
            if mins is not None and mins < cooldown:
                remaining = cooldown - mins
                return False, (
                    f"Debes esperar {remaining:.1f} minutos antes de volver a ejecutar "
                    f"este problema."
                )
        return True, ""

    # ── Worker ────────────────────────────────────────────────────────────────

    def _worker_loop(self):
        while not self._stop.is_set():
            run = self.db.claim_next_run()
            if run is None:
                self._wake.wait(timeout=3.0)
                self._wake.clear()
                continue
            try:
                self._execute(run)
            except Exception:
                log.exception("Fallo no controlado procesando el run %s", run["id"])
                try:
                    self.db.fail_run(run["id"], "Error interno del servidor")
                except Exception:
                    log.exception("No se pudo marcar el run como fallido")
            finally:
                # Puede haber quedado sitio para otro run en cola
                self._wake.set()

    def _execute(self, run: dict):
        run_id = run["id"]
        team_id = run["team_id"]
        slug = run["problem_slug"]
        label = f"{run['team_name']}/{slug}"

        self._emit(run, "running", "starting")

        problem = problems.get(slug)
        if problem is None:
            self.db.fail_run(run_id, f"El problema '{slug}' ya no está disponible.")
            self._emit(run, "error", "error")
            return

        submission = (
            self.db.get_submission(run["submission_id"]) if run["submission_id"] else None
        )
        if not submission:
            self.db.fail_run(run_id, "No se encontró la entrega asociada al run.")
            self._emit(run, "error", "error")
            return

        submission_dir = Path(submission["dir_path"])
        started = time.time()

        def on_phase(phase: str):
            self.db.update_run(run_id, phase=phase)
            self._emit(run, "running", phase)

        try:
            outcome = run_submission(
                problem, submission_dir, team_id, label=label, on_phase=on_phase,
            )
        except RunnerError as e:
            log.warning("[%s] evaluación fallida (%s): %s", label, e.blame, e)
            self.db.fail_run(run_id, str(e))
            self._emit(run, "error", "error", {"error": str(e)[:500]})
            return
        except Exception as e:
            log.exception("[%s] error inesperado", label)
            self.db.fail_run(run_id, f"Error inesperado: {e}")
            self._emit(run, "error", "error", {"error": str(e)[:500]})
            return

        score = problem.score(outcome.result)
        self.db.finish_run(run_id, outcome.result, score, outcome.log)
        log.info("[%s] completado en %.1fs — score %.4f", label,
                 time.time() - started, score)
        self._emit(run, "done", "done", {"score": score})

    def _emit(self, run: dict, status: str, phase: str, extra: dict | None = None):
        payload = {
            "run_id": run["id"],
            "problem": run["problem_slug"],
            "team_id": run["team_id"],
            "team_name": run.get("team_name"),
            "status": status,
            "phase": phase,
            "phase_label": PHASE_LABELS.get(phase, phase),
        }
        payload.update(extra or {})
        events.publish_team(run["team_id"], "run_update", payload)
