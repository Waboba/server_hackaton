"""
evaluator.py — Problema 2.1.1, biomasa(S, L, U, j). Corre DENTRO del contenedor.

Cada instancia es un conjunto de llamadas con las mismas S y j y con cotas
distintas (simulando knockeos), para que no se pueda cachear una sola solución.
Las cotas se preparan fuera del cronómetro; se mide solo el bucle de llamadas.
"""

import numpy as np

from medicion import correr

SIMBOLO = "biomasa"


def cargar_datos(ctx):
    """S, L, U del material docente. Si faltan, es culpa de la configuración."""
    carpeta = ctx.dataset("knockeos")
    params = ctx.params
    rutas = {
        "S": carpeta / str(params.get("archivo_S", "S_matriz.npy")),
        "L": carpeta / str(params.get("archivo_L", "L.npy")),
        "U": carpeta / str(params.get("archivo_U", "U.npy")),
    }
    faltan = [str(p.name) for p in rutas.values() if not p.exists()]
    if faltan:
        raise RuntimeError(
            "Faltan los datos del problema en problems/_datos_knockeos/: "
            + ", ".join(faltan)
            + ". Es un problema de configuración, avisa al organizador."
        )
    S = np.load(rutas["S"], allow_pickle=False)
    L = np.load(rutas["L"], allow_pickle=False).astype(float).ravel()
    U = np.load(rutas["U"], allow_pickle=False).astype(float).ravel()
    return np.asarray(S, dtype=float), L, U


def cotas_con_knockeos(L, U, anuladas, protegidos, rng):
    """Copia de (L, U) con `anuladas` reacciones forzadas a cero."""
    Lk, Uk = L.copy(), U.copy()
    if anuladas <= 0:
        return Lk, Uk
    candidatos = np.setdiff1d(np.arange(L.size), np.asarray(protegidos, dtype=int))
    elegidos = rng.choice(candidatos, size=min(anuladas, candidatos.size),
                          replace=False)
    Lk[elegidos] = 0.0
    Uk[elegidos] = 0.0
    return Lk, Uk


def evaluate(ctx) -> dict:
    params = ctx.params
    team_fn = ctx.require("solution.py", SIMBOLO)

    S, L, U = cargar_datos(ctx)
    j = int(params.get("indice_alcohol", 472))
    protegidos = [j, int(params.get("indice_biomasa", 1520))]
    semilla = int(params.get("semilla", 20260930))
    instancias = list(params.get("instancias", []))

    def preparar(spec):
        rng = np.random.default_rng(semilla + int(spec["llamadas"]))
        anuladas = int(spec.get("anuladas", 0))
        return [cotas_con_knockeos(L, U, anuladas, protegidos, rng)
                for _ in range(int(spec["llamadas"]))]

    def llamar(spec, lotes):
        return [team_fn(S, Lk, Uk, j) for Lk, Uk in lotes]

    def describir(spec):
        return (f"{int(spec['llamadas'])} llamada(s), "
                f"{int(spec.get('anuladas', 0))} reacción(es) anulada(s), j={j}")

    return correr(
        instancias, preparar, llamar,
        umbral_factor=float(params.get("umbral_factor", 3.0)),
        describir=describir,
    )
