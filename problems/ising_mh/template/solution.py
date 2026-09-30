"""
solution.py — Plantilla de entrega del problema 1.2 (IsingMH).

Debe definir IsingMH(N, beta, nf, X0) y devolver el estado de la cadena tras
nf pasos. Esta implementación es correcta pero deliberadamente ingenua: el
puntaje es el tiempo, así que el trabajo está en hacerla rápida.

Cadena: se propone voltear un sitio elegido uniformemente entre los que no son
borde y se acepta con probabilidad min(1, exp(-2*beta*sigma(i,j)*S)), donde S
es la suma de los vecinos.
"""

import math
import random

import numpy as np


def IsingMH(N, beta, nf, X0):
    X = np.array(X0, dtype=np.int64, copy=True)

    for _ in range(nf):
        i = random.randrange(N)
        j = random.randrange(1, N - 1)          # columnas 0 y N-1 son borde fijo

        s = 0
        if i > 0:
            s += X[i - 1, j]
        if i < N - 1:
            s += X[i + 1, j]
        s += X[i, j - 1]
        s += X[i, j + 1]

        delta = 2 * beta * X[i, j] * s          # -(H(propuesto) - H(actual))*beta
        if delta <= 0 or random.random() < math.exp(-delta):
            X[i, j] = -X[i, j]

    return X
