"""
solution.py — Plantilla de entrega del problema 2.1.1 (biomasa).

Debe definir biomasa(S, L, U, j) y devolver (óptimo de v_biomasa, v_j) o None si
el problema es infactible. Implementación correcta pero directa: arma el LP
completo y llama a linprog en cada llamada. El puntaje es el tiempo.

Índices del enunciado: biomasa = 1520, alcohol = 472.
"""

import numpy as np
from scipy.optimize import linprog

INDICE_BIOMASA = 1520


def biomasa(S, L, U, j):
    S = np.asarray(S, dtype=float)
    M = S.shape[1]

    c = np.zeros(M)
    c[INDICE_BIOMASA] = -1.0          # linprog minimiza: -v_biomasa = max v_biomasa

    res = linprog(
        c,
        A_eq=S, b_eq=np.zeros(S.shape[0]),
        bounds=list(zip(np.asarray(L, dtype=float), np.asarray(U, dtype=float))),
        method="highs",
    )

    if not res.success:
        return None

    return float(-res.fun), float(res.x[j])
