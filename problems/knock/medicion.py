"""
medicion.py — Cronómetro de instancias con corte por umbral.
Corre DENTRO del contenedor, junto a evaluator.py.

Este módulo es idéntico en todos los problemas de tipo «tiempo»: mide cuánto
tarda la función del equipo en cada instancia, una sola pasada por instancia, y
corta la llamada si supera `umbral_factor` veces el tiempo objetivo.

El corte se hace con SIGALRM, así que interrumpe de verdad a mitad de la
llamada (no hay que esperar a que el código del equipo termine).

Esquema del dict que devuelve correr():

    {
      "instancias": [
         {"nombre", "descripcion", "objetivo_secs", "limite_secs",
          "segundos", "estado", "error", "salida"}
      ],
      "segundos_total": float,      # suma de las instancias completadas
      "completadas": int,
      "total_instancias": int,
      "estado": "ok" | "excedido" | "error",
      "instancia_fallida": str | None,
      "umbral_factor": float,
    }
"""

import signal
import time


class TiempoExcedido(BaseException):
    """La llamada superó el límite y fue interrumpida.

    Hereda de BaseException a propósito: un `except Exception` dentro del
    código del equipo no la silencia.
    """


def _alarma(signum, frame):
    raise TiempoExcedido()


def limite(objetivo_secs, factor) -> float:
    """Segundos tras los que se corta la llamada. 0 = sin corte."""
    objetivo = float(objetivo_secs or 0.0)
    if objetivo <= 0:
        return 0.0
    return objetivo * float(factor)


def medir(fn, limite_secs: float = 0.0):
    """
    Ejecuta fn() midiendo tiempo de pared.

    Devuelve (segundos, valor, estado, detalle_error) con
    estado ∈ {"ok", "excedido", "error"}.
    """
    previo = None
    if limite_secs > 0:
        previo = signal.signal(signal.SIGALRM, _alarma)
        signal.setitimer(signal.ITIMER_REAL, float(limite_secs))

    valor, estado, detalle = None, "ok", None
    inicio = time.perf_counter()
    try:
        valor = fn()
    except TiempoExcedido:
        estado = "excedido"
    except Exception as e:
        estado, detalle = "error", f"{type(e).__name__}: {e}"[:300]
    finally:
        segundos = time.perf_counter() - inicio
        if limite_secs > 0:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previo)

    return segundos, valor, estado, detalle


def describir_salida(valor) -> str:
    """Descripción barata de lo que devolvió el equipo. Informativa, no se
    puntúa: este problema solo mide tiempo."""
    try:
        forma = getattr(valor, "shape", None)
        if forma is not None:
            return (f"{type(valor).__name__} shape={tuple(forma)} "
                    f"dtype={getattr(valor, 'dtype', '?')}")
        if isinstance(valor, (list, tuple)):
            return f"{type(valor).__name__} len={len(valor)}"
        if valor is None:
            return "None"
        return f"{type(valor).__name__} = {str(valor)[:60]}"
    except Exception:
        return "(no se pudo describir)"


def correr(instancias, preparar, llamar, umbral_factor=3.0, describir=None) -> dict:
    """
    Recorre las instancias en orden y cronometra una llamada por instancia.

    preparar(spec) -> entrada     se ejecuta FUERA del cronómetro
    llamar(spec, entrada)         la llamada al código del equipo; se cronometra
    describir(spec) -> str        texto para la página del run (opcional)

    En cuanto una instancia falla o excede el límite se abandonan las
    restantes: el run ya no puede puntuar y no tiene sentido ocupar la cola.
    """
    if not instancias:
        raise ValueError("el manifest no declara [[params.instancias]]")

    filas = []
    total = 0.0
    completadas = 0
    estado_global = "ok"
    fallida = None

    for spec in instancias:
        entrada = preparar(spec)
        lim = limite(spec.get("objetivo_secs", 0.0), umbral_factor)

        segundos, valor, estado, detalle = medir(
            lambda: llamar(spec, entrada), lim
        )

        filas.append({
            "nombre": str(spec.get("nombre", "?")),
            "descripcion": describir(spec) if describir else "",
            "objetivo_secs": float(spec.get("objetivo_secs", 0.0) or 0.0),
            "limite_secs": round(lim, 4),
            "segundos": round(segundos, 4),
            "estado": estado,
            "error": detalle,
            "salida": describir_salida(valor) if estado == "ok" else None,
        })

        if estado == "ok":
            total += segundos
            completadas += 1
            continue

        estado_global = estado
        fallida = filas[-1]["nombre"]
        break

    return {
        "instancias": filas,
        "segundos_total": round(total, 4),
        "completadas": completadas,
        "total_instancias": len(instancias),
        "estado": estado_global,
        "instancia_fallida": fallida,
        "umbral_factor": float(umbral_factor),
    }
