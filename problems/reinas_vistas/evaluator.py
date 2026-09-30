"""
evaluator.py — Problema 2.2.1, Vistas(Tablero). Corre DENTRO del contenedor.

Una sola llamada a Vistas es demasiado corta para medirla con sentido, así que
cada instancia es un lote de tableros: se cronometra el bucle completo. Los
tableros se generan fuera del reloj.
"""

import numpy as np

from medicion import correr

SIMBOLO = "Vistas"


def tablero(N: int, rng) -> np.ndarray:
    """Matriz N x N con exactamente N unos en celdas distintas."""
    M = np.zeros((N, N), dtype=np.int64)
    celdas = rng.choice(N * N, size=N, replace=False)
    M.reshape(-1)[celdas] = 1
    return M


def evaluate(ctx) -> dict:
    params = ctx.params
    team_fn = ctx.require("solution.py", SIMBOLO)

    semilla = int(params.get("semilla", 20260930))
    instancias = list(params.get("instancias", []))

    def preparar(spec):
        N = int(spec["N"])
        rng = np.random.default_rng(semilla + N)
        return [tablero(N, rng) for _ in range(int(spec["tableros"]))]

    def llamar(spec, tableros):
        return [team_fn(M) for M in tableros]

    def describir(spec):
        return f"N={int(spec['N'])}  lote de {int(spec['tableros']):,} tableros"

    return correr(
        instancias, preparar, llamar,
        umbral_factor=float(params.get("umbral_factor", 3.0)),
        describir=describir,
    )
