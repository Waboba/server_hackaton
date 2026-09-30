"""
solution.py — Plantilla de entrega del problema 1.4 (IsingG).

Debe definir IsingG(N, beta, nf, X0) y devolver el estado de la cadena tras nf
pasos. Implementación correcta pero ingenua a propósito: el puntaje es el
tiempo, así que el trabajo está en hacerla rápida.

Dinámica de Glauber: se elige un sitio uniformemente (sin tocar el borde fijo)
y se le asigna +1 con probabilidad exp(beta*S)/(exp(beta*S)+exp(-beta*S)),
donde S es la suma de sus vecinos.
"""

import math
import random

import numpy as np


def IsingG(N, beta, nf, X0):
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

        p_mas = 1.0 / (1.0 + math.exp(-2.0 * beta * s))
        X[i, j] = 1 if random.random() < p_mas else -1

    return X
