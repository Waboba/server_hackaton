"""
events.py — Bus de eventos en memoria para los streams SSE.

Dos canales:
  "team:<id>"  eventos de un equipo (estado de sus runs, cola)
  "admin"      todo, incluidas conexiones y desconexiones de dispositivos

La información de red se publica ÚNICAMENTE en el canal admin.
"""

import json
import queue
import threading

_subscribers: list[tuple[str, queue.Queue]] = []
_lock = threading.Lock()

MAX_BACKLOG = 100


def subscribe(channel: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=MAX_BACKLOG)
    with _lock:
        _subscribers.append((channel, q))
    return q


def unsubscribe(q: queue.Queue):
    with _lock:
        for i, (_, sub) in enumerate(_subscribers):
            if sub is q:
                _subscribers.pop(i)
                return


def publish(channel: str, kind: str, payload: dict | None = None):
    message = json.dumps({"kind": kind, "data": payload or {}})
    with _lock:
        targets = [q for ch, q in _subscribers if ch == channel]
    for q in targets:
        try:
            q.put_nowait(message)
        except queue.Full:
            pass  # cliente lento: se descarta el evento, el siguiente refresco lo corrige


def publish_admin(kind: str, payload: dict | None = None):
    publish("admin", kind, payload)


def publish_team(team_id: int, kind: str, payload: dict | None = None):
    publish(f"team:{team_id}", kind, payload)
    publish("admin", kind, payload)


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)
