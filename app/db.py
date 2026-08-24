"""
db.py — Acceso a SQLite.

Tablas:
  teams            equipos
  devices          dispositivos (MAC) de la whitelist, varios por equipo
  device_events    historial de conexiones y desconexiones
  unknown_devices  MAC vistas en la red o intentando entrar sin estar dadas de alta
  problems         estado (activo/abierto) de cada plugin de problems/
  submissions      entregas guardadas
  runs             cola y resultados de evaluación
  settings         ajustes en caliente (submissions abiertas, secreto de sesión)
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.config import DB_PATH

_write_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_local(value: str | None) -> str:
    """Timestamp ISO (UTC) → texto legible en hora local del servidor."""
    dt = parse_iso(value)
    if not dt:
        return "—"
    return dt.astimezone().strftime("%d/%m %H:%M:%S")


def ago(value: str | None) -> str:
    """Timestamp ISO → 'hace 3m 20s'."""
    dt = parse_iso(value)
    if not dt:
        return "—"
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 60:
        return f"hace {secs}s"
    mins, s = divmod(secs, 60)
    if mins < 60:
        return f"hace {mins}m {s}s"
    hours, m = divmod(mins, 60)
    if hours < 24:
        return f"hace {hours}h {m}m"
    return f"hace {hours // 24}d"


SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    mac         TEXT PRIMARY KEY,
    team_id     INTEGER,
    label       TEXT NOT NULL DEFAULT '',
    is_admin    INTEGER NOT NULL DEFAULT 0,
    added_at    TEXT NOT NULL,
    last_seen   TEXT,
    last_ip     TEXT,
    status      TEXT NOT NULL DEFAULT 'offline',
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS device_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mac         TEXT NOT NULL,
    team_id     INTEGER,
    team_name   TEXT,
    label       TEXT,
    event       TEXT NOT NULL,          -- connect | disconnect
    ip          TEXT,
    ts          TEXT NOT NULL,
    seen        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON device_events(ts DESC);

CREATE TABLE IF NOT EXISTS unknown_devices (
    mac         TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    last_ip     TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,   -- intentos de acceso a la web
    source      TEXT NOT NULL DEFAULT 'scan'  -- scan | http
);

CREATE TABLE IF NOT EXISTS problems (
    slug        TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    is_open     INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id       INTEGER NOT NULL,
    problem_slug  TEXT NOT NULL,
    dir_path      TEXT NOT NULL,
    mac           TEXT,
    ts            TEXT NOT NULL,
    consumed      INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sub_team ON submissions(team_id, problem_slug);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id       INTEGER NOT NULL,
    problem_slug  TEXT NOT NULL,
    submission_id INTEGER,
    status        TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|error|cancelled
    phase         TEXT NOT NULL DEFAULT 'queued',
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    result_json   TEXT,
    score         REAL,
    error_msg     TEXT,
    log           TEXT,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_runs_team ON runs(team_id, problem_slug, id DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Settings ──────────────────────────────────────────────────────────────

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with _write_lock, self._conn() as c:
            c.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ── Equipos ───────────────────────────────────────────────────────────────

    def create_team(self, name: str) -> int:
        """Crea el equipo o devuelve el id del que ya existe con ese nombre."""
        name = name.strip()
        with _write_lock, self._conn() as c:
            row = c.execute("SELECT id FROM teams WHERE name = ?", (name,)).fetchone()
            if row:
                return row["id"]
            cur = c.execute(
                "INSERT INTO teams (name, created_at) VALUES (?, ?)", (name, now_iso())
            )
            return cur.lastrowid

    def get_team(self, team_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
        return dict(row) if row else None

    def get_team_by_name(self, name: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM teams WHERE name = ?", (name.strip(),)).fetchone()
        return dict(row) if row else None

    def list_teams(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("""
                SELECT t.*,
                       (SELECT COUNT(*) FROM devices d WHERE d.team_id = t.id) AS device_count,
                       (SELECT COUNT(*) FROM devices d
                         WHERE d.team_id = t.id AND d.status = 'online') AS online_count
                FROM teams t ORDER BY t.name COLLATE NOCASE
            """).fetchall()
        return [dict(r) for r in rows]

    def rename_team(self, team_id: int, name: str):
        with _write_lock, self._conn() as c:
            c.execute("UPDATE teams SET name = ? WHERE id = ?", (name.strip(), team_id))

    def delete_team(self, team_id: int):
        with _write_lock, self._conn() as c:
            c.execute("UPDATE devices SET team_id = NULL WHERE team_id = ?", (team_id,))
            c.execute("DELETE FROM runs WHERE team_id = ?", (team_id,))
            c.execute("DELETE FROM submissions WHERE team_id = ?", (team_id,))
            c.execute("DELETE FROM teams WHERE id = ?", (team_id,))

    # ── Dispositivos ──────────────────────────────────────────────────────────

    def add_device(self, mac: str, team_id: int | None, label: str = "",
                   is_admin: bool = False):
        mac = mac.strip().lower()
        with _write_lock, self._conn() as c:
            c.execute("""
                INSERT INTO devices (mac, team_id, label, is_admin, added_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(mac) DO UPDATE SET
                    team_id  = excluded.team_id,
                    label    = excluded.label,
                    is_admin = excluded.is_admin
            """, (mac, team_id, label.strip(), int(is_admin), now_iso()))
            c.execute("DELETE FROM unknown_devices WHERE mac = ?", (mac,))

    def get_device(self, mac: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("""
                SELECT d.*, t.name AS team_name
                FROM devices d LEFT JOIN teams t ON t.id = d.team_id
                WHERE d.mac = ?
            """, (mac.strip().lower(),)).fetchone()
        return dict(row) if row else None

    def list_devices(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("""
                SELECT d.*, t.name AS team_name
                FROM devices d LEFT JOIN teams t ON t.id = d.team_id
                ORDER BY (d.status = 'online') DESC,
                         t.name COLLATE NOCASE, d.label COLLATE NOCASE, d.mac
            """).fetchall()
        return [dict(r) for r in rows]

    def delete_device(self, mac: str):
        with _write_lock, self._conn() as c:
            c.execute("DELETE FROM devices WHERE mac = ?", (mac.strip().lower(),))

    def touch_device(self, mac: str, ip: str | None):
        """Marca actividad del dispositivo sin cambiar su estado online/offline."""
        with _write_lock, self._conn() as c:
            c.execute(
                "UPDATE devices SET last_seen = ?, last_ip = COALESCE(?, last_ip) WHERE mac = ?",
                (now_iso(), ip, mac.strip().lower()),
            )

    def set_device_status(self, mac: str, status: str, ip: str | None = None):
        with _write_lock, self._conn() as c:
            c.execute("""
                UPDATE devices SET status = ?, last_seen = ?,
                       last_ip = COALESCE(?, last_ip)
                WHERE mac = ?
            """, (status, now_iso(), ip, mac.strip().lower()))

    def count_devices(self) -> tuple[int, int]:
        with self._conn() as c:
            row = c.execute("""
                SELECT COUNT(*) AS total,
                       SUM(status = 'online') AS online FROM devices
            """).fetchone()
        return row["total"] or 0, row["online"] or 0

    # ── Eventos de red ────────────────────────────────────────────────────────

    def add_device_event(self, mac: str, event: str, ip: str | None,
                         team_id: int | None, team_name: str | None,
                         label: str | None) -> dict:
        ts = now_iso()
        with _write_lock, self._conn() as c:
            cur = c.execute("""
                INSERT INTO device_events (mac, team_id, team_name, label, event, ip, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (mac, team_id, team_name, label, event, ip, ts))
            event_id = cur.lastrowid
        return {
            "id": event_id, "mac": mac, "team_id": team_id, "team_name": team_name,
            "label": label, "event": event, "ip": ip, "ts": ts,
        }

    def list_device_events(self, limit: int = 200, mac: str | None = None,
                           team_id: int | None = None,
                           event: str | None = None) -> list[dict]:
        where, params = [], []
        if mac:
            where.append("mac = ?")
            params.append(mac.lower())
        if team_id is not None:
            where.append("team_id = ?")
            params.append(team_id)
        if event:
            where.append("event = ?")
            params.append(event)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        params.append(limit)
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM device_events {clause} ORDER BY id DESC LIMIT ?", params
            ).fetchall()
        return [dict(r) for r in rows]

    def unseen_event_count(self) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM device_events WHERE seen = 0"
            ).fetchone()
        return row["n"]

    def mark_events_seen(self):
        with _write_lock, self._conn() as c:
            c.execute("UPDATE device_events SET seen = 1 WHERE seen = 0")

    # ── Dispositivos desconocidos ─────────────────────────────────────────────

    def touch_unknown(self, mac: str, ip: str | None, source: str = "scan"):
        mac = mac.strip().lower()
        ts = now_iso()
        with _write_lock, self._conn() as c:
            if c.execute("SELECT 1 FROM devices WHERE mac = ?", (mac,)).fetchone():
                return
            c.execute("""
                INSERT INTO unknown_devices (mac, first_seen, last_seen, last_ip, attempts, source)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(mac) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    last_ip   = COALESCE(excluded.last_ip, unknown_devices.last_ip),
                    attempts  = unknown_devices.attempts + excluded.attempts,
                    source    = CASE WHEN excluded.source = 'http' THEN 'http'
                                     ELSE unknown_devices.source END
            """, (mac, ts, ts, ip, 1 if source == "http" else 0, source))

    def list_unknown(self, limit: int = 100) -> list[dict]:
        """
        En una red compartida el barrido detecta muchos dispositivos ajenos al
        evento. Primero los que han intentado entrar a la web: son los que el
        organizador necesita dar de alta.
        """
        with self._conn() as c:
            rows = c.execute("""
                SELECT * FROM unknown_devices
                ORDER BY (source = 'http') DESC, attempts DESC, last_seen DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def delete_unknown(self, mac: str):
        with _write_lock, self._conn() as c:
            c.execute("DELETE FROM unknown_devices WHERE mac = ?", (mac.strip().lower(),))

    # ── Problemas ─────────────────────────────────────────────────────────────

    def sync_problem(self, slug: str, title: str, enabled_default: bool):
        """Registra el problema si es nuevo; respeta el estado ya guardado."""
        with _write_lock, self._conn() as c:
            c.execute("""
                INSERT INTO problems (slug, title, enabled, is_open, updated_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(slug) DO UPDATE SET title = excluded.title
            """, (slug, title, int(enabled_default), now_iso()))

    def get_problem_state(self, slug: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM problems WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def list_problem_states(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM problems ORDER BY title COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]

    def set_problem_flags(self, slug: str, enabled: bool | None = None,
                          is_open: bool | None = None):
        sets, params = [], []
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(int(enabled))
        if is_open is not None:
            sets.append("is_open = ?")
            params.append(int(is_open))
        if not sets:
            return
        sets.append("updated_at = ?")
        params += [now_iso(), slug]
        with _write_lock, self._conn() as c:
            c.execute(f"UPDATE problems SET {', '.join(sets)} WHERE slug = ?", params)

    # ── Entregas ──────────────────────────────────────────────────────────────

    def add_submission(self, team_id: int, slug: str, dir_path: str,
                       mac: str | None) -> int:
        with _write_lock, self._conn() as c:
            cur = c.execute("""
                INSERT INTO submissions (team_id, problem_slug, dir_path, mac, ts)
                VALUES (?, ?, ?, ?, ?)
            """, (team_id, slug, dir_path, mac, now_iso()))
        return cur.lastrowid

    def latest_submission(self, team_id: int, slug: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("""
                SELECT * FROM submissions
                WHERE team_id = ? AND problem_slug = ?
                ORDER BY id DESC LIMIT 1
            """, (team_id, slug)).fetchone()
        return dict(row) if row else None

    def get_submission(self, submission_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM submissions WHERE id = ?",
                            (submission_id,)).fetchone()
        return dict(row) if row else None

    def consume_submission(self, submission_id: int):
        with _write_lock, self._conn() as c:
            c.execute("UPDATE submissions SET consumed = 1 WHERE id = ?", (submission_id,))

    # ── Runs ──────────────────────────────────────────────────────────────────

    def create_run(self, team_id: int, slug: str, submission_id: int | None) -> int:
        with _write_lock, self._conn() as c:
            cur = c.execute("""
                INSERT INTO runs (team_id, problem_slug, submission_id, status, phase, created_at)
                VALUES (?, ?, ?, 'queued', 'queued', ?)
            """, (team_id, slug, submission_id, now_iso()))
        return cur.lastrowid

    def claim_next_run(self) -> dict | None:
        """Toma atómicamente el siguiente run en cola y lo marca como running."""
        with _write_lock, self._conn() as c:
            row = c.execute(
                "SELECT id FROM runs WHERE status = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            cur = c.execute("""
                UPDATE runs SET status = 'running', phase = 'starting', started_at = ?
                WHERE id = ? AND status = 'queued'
            """, (now_iso(), row["id"]))
            if cur.rowcount == 0:
                return None
            claimed = c.execute("""
                SELECT r.*, t.name AS team_name FROM runs r
                JOIN teams t ON t.id = r.team_id WHERE r.id = ?
            """, (row["id"],)).fetchone()
        return dict(claimed) if claimed else None

    def update_run(self, run_id: int, **fields):
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with _write_lock, self._conn() as c:
            c.execute(f"UPDATE runs SET {sets} WHERE id = ?",
                      list(fields.values()) + [run_id])

    def finish_run(self, run_id: int, result: dict, score: float, log: str = ""):
        self.update_run(
            run_id, status="done", phase="done", finished_at=now_iso(),
            result_json=json.dumps(result), score=score, log=log[-8000:], error_msg=None,
        )

    def fail_run(self, run_id: int, error: str, log: str = ""):
        self.update_run(
            run_id, status="error", phase="error", finished_at=now_iso(),
            error_msg=error[:2000], log=log[-8000:],
        )

    def cancel_run(self, run_id: int) -> bool:
        with _write_lock, self._conn() as c:
            cur = c.execute("""
                UPDATE runs SET status = 'cancelled', phase = 'cancelled', finished_at = ?
                WHERE id = ? AND status = 'queued'
            """, (now_iso(), run_id))
        return cur.rowcount > 0

    def get_run(self, run_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("""
                SELECT r.*, t.name AS team_name FROM runs r
                JOIN teams t ON t.id = r.team_id WHERE r.id = ?
            """, (run_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("result_json"):
            d["result"] = json.loads(d["result_json"])
        return d

    def last_run(self, team_id: int, slug: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("""
                SELECT * FROM runs WHERE team_id = ? AND problem_slug = ?
                ORDER BY id DESC LIMIT 1
            """, (team_id, slug)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("result_json"):
            d["result"] = json.loads(d["result_json"])
        return d

    def team_runs(self, team_id: int, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("""
                SELECT * FROM runs WHERE team_id = ? ORDER BY id DESC LIMIT ?
            """, (team_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def has_pending_run(self, team_id: int, slug: str) -> bool:
        with self._conn() as c:
            row = c.execute("""
                SELECT 1 FROM runs
                WHERE team_id = ? AND problem_slug = ? AND status IN ('queued', 'running')
                LIMIT 1
            """, (team_id, slug)).fetchone()
        return row is not None

    def queue_snapshot(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("""
                SELECT r.*, t.name AS team_name FROM runs r
                JOIN teams t ON t.id = r.team_id
                WHERE r.status IN ('queued', 'running')
                ORDER BY (r.status = 'running') DESC, r.id
            """).fetchall()
        return [dict(r) for r in rows]

    def queue_position(self, run_id: int) -> int | None:
        with self._conn() as c:
            row = c.execute("""
                SELECT COUNT(*) AS n FROM runs
                WHERE status = 'queued' AND id < ?
            """, (run_id,)).fetchone()
        return (row["n"] or 0) + 1

    def reset_orphan_runs(self) -> int:
        """Runs que quedaron 'running' por un corte del servidor."""
        with _write_lock, self._conn() as c:
            cur = c.execute("""
                UPDATE runs SET status = 'error', phase = 'error', finished_at = ?,
                       error_msg = 'Interrumpido por reinicio del servidor'
                WHERE status = 'running'
            """, (now_iso(),))
        return cur.rowcount

    def minutes_since_last_run(self, team_id: int, slug: str) -> float | None:
        with self._conn() as c:
            row = c.execute("""
                SELECT finished_at FROM runs
                WHERE team_id = ? AND problem_slug = ? AND status = 'done'
                ORDER BY id DESC LIMIT 1
            """, (team_id, slug)).fetchone()
        if not row or not row["finished_at"]:
            return None
        dt = parse_iso(row["finished_at"])
        if not dt:
            return None
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60

    def reset_cooldown(self, team_id: int, slug: str):
        with _write_lock, self._conn() as c:
            c.execute("""
                UPDATE runs SET finished_at = NULL
                WHERE id = (SELECT id FROM runs
                            WHERE team_id = ? AND problem_slug = ? AND status = 'done'
                            ORDER BY id DESC LIMIT 1)
            """, (team_id, slug))

    # ── Leaderboard ───────────────────────────────────────────────────────────

    def leaderboard_runs(self, slug: str, ranking: str = "last") -> list[dict]:
        """
        Un run por equipo: el último completado ('last') o el de mayor score ('best').
        El cálculo del score lo hace el plugin del problema; aquí solo se ordena
        por el score ya persistido.
        """
        if ranking == "best":
            order = "r.score DESC, r.id ASC"
            pick = """
                r.id = (SELECT r2.id FROM runs r2
                        WHERE r2.team_id = r.team_id AND r2.problem_slug = r.problem_slug
                          AND r2.status = 'done'
                        ORDER BY r2.score DESC, r2.id ASC LIMIT 1)
            """
        else:
            order = "r.score DESC, r.finished_at ASC"
            pick = """
                r.id = (SELECT MAX(r2.id) FROM runs r2
                        WHERE r2.team_id = r.team_id AND r2.problem_slug = r.problem_slug
                          AND r2.status = 'done')
            """
        with self._conn() as c:
            rows = c.execute(f"""
                SELECT r.*, t.name AS team_name FROM runs r
                JOIN teams t ON t.id = r.team_id
                WHERE r.problem_slug = ? AND r.status = 'done' AND {pick}
                ORDER BY {order}
            """, (slug,)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["result"] = json.loads(d["result_json"]) if d["result_json"] else {}
            out.append(d)
        return out

    # ── Estadísticas ──────────────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._conn() as c:
            teams = c.execute("SELECT COUNT(*) AS n FROM teams").fetchone()["n"]
            statuses = c.execute(
                "SELECT status, COUNT(*) AS n FROM runs GROUP BY status"
            ).fetchall()
            devices = c.execute("""
                SELECT COUNT(*) AS total, SUM(status = 'online') AS online FROM devices
            """).fetchone()
            unknown = c.execute("SELECT COUNT(*) AS n FROM unknown_devices").fetchone()["n"]
        counts = {r["status"]: r["n"] for r in statuses}
        return {
            "teams": teams,
            "devices_total": devices["total"] or 0,
            "devices_online": devices["online"] or 0,
            "unknown": unknown,
            "run_done": counts.get("done", 0),
            "run_error": counts.get("error", 0),
            "run_running": counts.get("running", 0),
            "run_queued": counts.get("queued", 0),
        }
