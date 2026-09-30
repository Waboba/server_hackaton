"""
evaluator.py — Problema 2.2.3, Reinas(beta, k, M0). Corre DENTRO del contenedor.

Solo cronometra: prepara M0 fuera del reloj, llama una vez a Reinas por
instancia y devuelve el esquema común de medicion.correr().
"""

import numpy as np

from medicion import correr

SIMBOLO = "Reinas"


def tablero_inicial(N: int, semilla: int) -> np.ndarray:
    """N reinas, una por fila, en columnas aleatorias (permutación no forzada)."""
    rng = np.random.default_rng(semilla)
    M0 = np.zeros((N, N), dtype=np.int64)
    M0[np.arange(N), rng.integers(0, N, size=N)] = 1
    # Si dos reinas cayeron en la misma celda no puede pasar (una por fila),
    # así que el tablero siempre tiene exactamente N unos.
    return M0


def evaluate(ctx) -> dict:
    params = ctx.params
    team_fn = ctx.require("solution.py", SIMBOLO)

    semilla = int(params.get("semilla", 20260930))
    instancias = list(params.get("instancias", []))

    def preparar(spec):
        return tablero_inicial(int(spec["N"]), semilla + int(spec["N"]))

    def llamar(spec, M0):
        return team_fn(float(spec["beta"]), int(spec["k"]), M0)

    def describir(spec):
        return (f"N={int(spec['N'])}  beta={float(spec['beta']):.6g}  "
                f"k={int(spec['k']):,}")

    return correr(
        instancias, preparar, llamar,
        umbral_factor=float(params.get("umbral_factor", 3.0)),
        describir=describir,
    )
