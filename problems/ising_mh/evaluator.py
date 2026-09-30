"""
evaluator.py — Problema 1.2, IsingMH. Corre DENTRO del contenedor.

Solo cronometra: prepara X0 fuera del reloj, llama una vez a IsingMH por
instancia y devuelve el esquema común de medicion.correr().
"""

import numpy as np

from medicion import correr

SIMBOLO = "IsingMH"


def estado_inicial(N: int, semilla: int) -> np.ndarray:
    """X0 uniforme en {-1,1}^(NxN) con las columnas 0 y N-1 fijas a +1."""
    rng = np.random.default_rng(semilla)
    X0 = rng.integers(0, 2, size=(N, N), dtype=np.int64) * 2 - 1
    X0[:, 0] = 1
    X0[:, N - 1] = 1
    return X0


def evaluate(ctx) -> dict:
    params = ctx.params
    team_fn = ctx.require("solution.py", SIMBOLO)

    semilla = int(params.get("semilla", 20260930))
    instancias = list(params.get("instancias", []))

    def preparar(spec):
        # Fuera del cronómetro: generar X0 no es parte de lo que se mide.
        return estado_inicial(int(spec["N"]), semilla + int(spec["N"]))

    def llamar(spec, X0):
        return team_fn(int(spec["N"]), float(spec["beta"]), int(spec["nf"]), X0)

    def describir(spec):
        return (f"N={int(spec['N'])}  beta={float(spec['beta']):.6g}  "
                f"nf={int(spec['nf']):,}")

    return correr(
        instancias, preparar, llamar,
        umbral_factor=float(params.get("umbral_factor", 3.0)),
        describir=describir,
    )
