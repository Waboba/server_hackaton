"""
evaluator.py — Evaluación del problema de huellas. Corre DENTRO del contenedor.

Contrato del sistema:

    def evaluate(ctx) -> dict

    ctx.params            parámetros de [params] en manifest.toml
    ctx.dataset(nombre)   Path al dataset montado read-only
    ctx.require(archivo, símbolo)  importa la entrega y devuelve el símbolo

El dict devuelto se guarda tal cual y es lo que reciben score() y summary()
en scoring.py, así que debe ser serializable a JSON.
"""

import random

import cv2
import numpy as np

from matchers import CombinedMatcher, evaluate_image, load_image


def eval_dataset(team_fn, image_paths, matcher, label: str) -> list[dict]:
    """Aplica la función del equipo a cada imagen y la compara con la original."""
    rows = []
    for path in image_paths:
        original = load_image(path)
        if original is None:
            rows.append({"file": path.name, "dataset": label, "a_accept": False,
                         "b_accept": False, "error": "No se pudo cargar"})
            continue

        try:
            modified = team_fn(original.copy())
            if modified is None or not isinstance(modified, np.ndarray):
                raise ValueError("main() no retornó una imagen válida (numpy array)")
            if modified.ndim == 3:
                modified = cv2.cvtColor(modified, cv2.COLOR_BGR2GRAY)
        except Exception as e:
            rows.append({"file": path.name, "dataset": label, "a_accept": False,
                         "b_accept": False, "error": str(e)[:120]})
            continue

        a_accept, b_accept = evaluate_image(matcher, modified, original)
        rows.append({"file": path.name, "dataset": label, "a_accept": a_accept,
                     "b_accept": b_accept, "error": None})
    return rows


def confusion(rows: list[dict]) -> dict:
    counts = {"(True, False)": 0, "(True, True)": 0,
              "(False, False)": 0, "(False, True)": 0}
    for row in rows:
        if row["error"]:
            continue
        counts[f"({row['a_accept']}, {row['b_accept']})"] += 1
    return counts


def evaluate(ctx) -> dict:
    params = ctx.params
    glob = params.get("image_glob", "*.tif")

    team_fn = ctx.require("solution.py", "main")
    matcher = CombinedMatcher(params)

    # k: todas las imágenes
    k_files = sorted(ctx.dataset("k").glob(glob))
    rows_k = eval_dataset(team_fn, k_files, matcher, "k")

    # k4: muestra aleatoria distinta en cada ejecución
    k4_files = sorted(ctx.dataset("k4").glob(glob))
    sample_size = min(int(params.get("k4_sample_count", 20)), len(k4_files))
    k4_sample = random.sample(k4_files, sample_size) if k4_files else []
    rows_k4 = eval_dataset(team_fn, k4_sample, matcher, "k4")

    # k5: todas, y solo cuenta si el 100 % sale A'✓ B'✗
    k5_files = sorted(ctx.dataset("k5").glob(glob))
    rows_k5 = eval_dataset(team_fn, k5_files, matcher, "k5")

    k5_valid = [r for r in rows_k5 if not r["error"]]
    k5_perfect = int(bool(k5_valid) and
                     all(r["a_accept"] and not r["b_accept"] for r in k5_valid))

    ck, ck4, ck5 = confusion(rows_k), confusion(rows_k4), confusion(rows_k5)
    return {
        "k_confusion": ck,
        "k_good_count": ck["(True, False)"],
        "k4_confusion": ck4,
        "k5_confusion": ck5,
        "k5_perfect": k5_perfect,
        "k_total": len(rows_k),
        "k4_total": len(rows_k4),
        "k5_total": len(rows_k5),
        "k_rows": rows_k,
        "k4_rows": rows_k4,
        "k5_rows": rows_k5,
    }
