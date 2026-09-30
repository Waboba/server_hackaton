"""
evaluator.py — Problema 2.1.3, Knock(beta, n, y0, K). Corre DENTRO del contenedor.

El código del equipo carga S, L y U por su cuenta desde /data/knockeos. Aquí
solo se lee S para saber el número de reacciones M y construir y0, y para
comprobar que los datos están donde deben.
"""

import numpy as np

from medicion import correr

SIMBOLO = "Knock"


def comprobar_datos(ctx):
    """Devuelve la ruta de S tras verificar que los tres archivos existen."""
    carpeta = ctx.dataset("knockeos")
    params = ctx.params
    rutas = [
        carpeta / str(params.get("archivo_S", "S_matriz.npy")),
        carpeta / str(params.get("archivo_L", "L.npy")),
        carpeta / str(params.get("archivo_U", "U.npy")),
    ]
    faltan = [p.name for p in rutas if not p.exists()]
    if faltan:
        raise RuntimeError(
            "Faltan los datos del problema en problems/_datos_knockeos/: "
            + ", ".join(faltan)
            + ". Es un problema de configuración, avisa al organizador."
        )
    return rutas[0]


def estado_inicial(M: int, K: int, protegidos, semilla: int) -> np.ndarray:
    """y0 de largo M con exactamente K ceros (los nutrientes suprimidos)."""
    rng = np.random.default_rng(semilla)
    y0 = np.ones(M, dtype=np.int64)
    candidatos = np.setdiff1d(np.arange(M), np.asarray(protegidos, dtype=int))
    y0[rng.choice(candidatos, size=min(K, candidatos.size), replace=False)] = 0
    return y0


def evaluate(ctx) -> dict:
    params = ctx.params
    team_fn = ctx.require("solution.py", SIMBOLO)

    ruta_S = comprobar_datos(ctx)
    M = int(np.load(ruta_S, allow_pickle=False, mmap_mode="r").shape[1])

    protegidos = [int(params.get("indice_alcohol", 472)),
                  int(params.get("indice_biomasa", 1520))]
    semilla = int(params.get("semilla", 20260930))
    instancias = list(params.get("instancias", []))

    def preparar(spec):
        K = int(spec["K"])
        return estado_inicial(M, K, protegidos, semilla + K)

    def llamar(spec, y0):
        return team_fn(float(spec["beta"]), int(spec["n"]), y0, int(spec["K"]))

    def describir(spec):
        return (f"K={int(spec['K'])}  n={int(spec['n'])}  "
                f"beta={float(spec['beta']):.6g}  M={M}")

    return correr(
        instancias, preparar, llamar,
        umbral_factor=float(params.get("umbral_factor", 3.0)),
        describir=describir,
    )
