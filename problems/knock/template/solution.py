"""
solution.py — Plantilla de entrega del problema 2.1.3 (Knock).

Debe definir Knock(beta, n, y0, K) y devolver el y final tras n pasos.
Implementación correcta pero directa: un LP completo por cada propuesta. El
puntaje es el tiempo, así que el trabajo está en hacerla rápida (reutilizar el
modelo, warm start, cachear energías ya vistas...).

Cadena (Metropolis-Hastings): se propone cambiar una supresión por otra
(intercambio de un 0 por un 1) y se acepta con min(1, exp(-beta*(E' - E))).
"""

import math
import os
import random
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

INDICE_BIOMASA = 1520
INDICE_ALCOHOL = 472
ETA = 0.288 * 0.4

# Los datos se cargan al importar el módulo: eso pasa FUERA del cronómetro.
CARPETA = Path(os.environ.get("DATASET_KNOCKEOS", "/data/knockeos"))
if not (CARPETA / "S_matriz.npy").exists():
    CARPETA = Path(".")          # para poder probar en tu propia máquina

S = np.load(CARPETA / "S_matriz.npy", allow_pickle=False).astype(float)
L = np.load(CARPETA / "L.npy", allow_pickle=False).astype(float).ravel()
U = np.load(CARPETA / "U.npy", allow_pickle=False).astype(float).ravel()


def energia(y):
    """E(y) = -v_alcohol si [Interior] es factible, +inf si no."""
    Ly = L * y
    Uy = U * y

    c = np.zeros(S.shape[1])
    c[INDICE_BIOMASA] = -1.0

    res = linprog(
        c,
        A_eq=S, b_eq=np.zeros(S.shape[0]),
        bounds=list(zip(Ly, Uy)),
        method="highs",
    )
    if not res.success or -res.fun < ETA:
        return math.inf
    return -float(res.x[INDICE_ALCOHOL])


def Knock(beta, n, y0, K):
    y = np.array(y0, dtype=np.int64, copy=True)
    actual = energia(y)

    for _ in range(n):
        suprimidos = np.nonzero(y == 0)[0]
        activos = np.nonzero(y == 1)[0]
        if suprimidos.size == 0 or activos.size == 0:
            break

        fuera = int(random.choice(suprimidos))      # se reactiva
        dentro = int(random.choice(activos))        # se suprime
        y[fuera], y[dentro] = 1, 0

        propuesta = energia(y)
        if propuesta == math.inf and actual == math.inf:
            aceptar = True                          # ambas infactibles: da igual
        elif propuesta == math.inf:
            aceptar = False
        elif actual == math.inf:
            aceptar = True
        else:
            delta = beta * (propuesta - actual)
            aceptar = delta <= 0 or random.random() < math.exp(-delta)

        if aceptar:
            actual = propuesta
        else:
            y[fuera], y[dentro] = 0, 1

    return y
