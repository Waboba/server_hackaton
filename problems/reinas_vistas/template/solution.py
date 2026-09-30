"""
solution.py — Plantilla de entrega del problema 2.2.1 (Vistas).

Debe definir Vistas(Tablero) y devolver R(M). Implementación correcta pero
ingenua: recorre todos los pares de reinas. El puntaje es el tiempo, así que
el trabajo está en hacerla rápida.
"""

import numpy as np


def Vistas(Tablero):
    filas, columnas = np.nonzero(np.asarray(Tablero))
    total = 0

    for a in range(len(filas)):
        for b in range(len(filas)):
            if a == b:
                continue
            i1, j1 = filas[a], columnas[a]
            i2, j2 = filas[b], columnas[b]
            if i1 == i2 or j1 == j2 or abs(i1 - i2) == abs(j1 - j2):
                total += 1

    return int(total)
