"""
solution.py — Plantilla de entrega del problema 2.2.3 (Reinas).

Debe definir Reinas(beta, k, M0) y devolver el tablero tras k pasos.
Implementación correcta pero ingenua: recalcula R(M) desde cero en cada paso.
El puntaje es el tiempo, así que el trabajo está en hacerla rápida.

Cadena (Metropolis-Hastings): se propone mover una reina elegida al azar a una
celda vacía elegida al azar y se acepta con probabilidad
min(1, exp(-beta*(R(nuevo) - R(actual)))).
"""

import math
import random

import numpy as np


def _R(filas, columnas):
    total = 0
    n = len(filas)
    for a in range(n):
        for b in range(n):
            if a == b:
                continue
            if (filas[a] == filas[b] or columnas[a] == columnas[b]
                    or abs(filas[a] - filas[b]) == abs(columnas[a] - columnas[b])):
                total += 1
    return total


def Reinas(beta, k, M0):
    M = np.array(M0, dtype=np.int64, copy=True)
    N = M.shape[0]

    fs, cs = np.nonzero(M)
    filas, columnas = list(fs), list(cs)
    ocupadas = set(zip(filas, columnas))
    actual = _R(filas, columnas)

    for _ in range(k):
        q = random.randrange(len(filas))
        i = random.randrange(N)
        j = random.randrange(N)
        if (i, j) in ocupadas:
            continue

        vieja = (filas[q], columnas[q])
        filas[q], columnas[q] = i, j
        propuesto = _R(filas, columnas)

        delta = beta * (propuesto - actual)
        if delta <= 0 or random.random() < math.exp(-delta):
            ocupadas.discard(vieja)
            ocupadas.add((i, j))
            actual = propuesto
        else:
            filas[q], columnas[q] = vieja

    M = np.zeros((N, N), dtype=np.int64)
    for i, j in zip(filas, columnas):
        M[i, j] = 1
    return M
