"""
scoring.py — Score y presentación de resultados. Corre EN EL SERVIDOR.

Contrato del sistema:

    SUMMARY_COLUMNS            columnas del leaderboard, en orden
    score(result, params)      -> float   (ordena el leaderboard; es público)
    summary(result, params)    -> dict    (valores de esas columnas)
    detail_blocks(result, p)   -> [(título, texto)]   (página del run)

`result` es exactamente lo que devolvió evaluate(ctx) en el contenedor.
"""

SUMMARY_COLUMNS = ["k", "k4", "k5"]


def _k4_good(result: dict) -> int:
    confusion = result.get("k4_confusion", {})
    return confusion.get("(True, False)", 0) if isinstance(confusion, dict) else 0


def score(result: dict, params: dict) -> float:
    """
    Combinación convexa de los tres datasets:
        alpha * (aciertos k / total k)
      + beta  * (aciertos k4 / total k4)
      + gamma * k5_perfect (0 o 1)
    """
    k_total = result.get("k_total", 0) or 1
    k4_total = result.get("k4_total", 0) or 1
    return (
        float(params.get("score_alpha", 0.5)) * (result.get("k_good_count", 0) / k_total)
        + float(params.get("score_beta", 0.25)) * (_k4_good(result) / k4_total)
        + float(params.get("score_gamma", 0.25)) * result.get("k5_perfect", 0)
    )


def summary(result: dict, params: dict) -> dict:
    k_total = result.get("k_total", 0) or 1
    k4_total = result.get("k4_total", 0) or 1
    k_good = result.get("k_good_count", 0)
    return {
        "k": f"{k_good}/{result.get('k_total', 0)} ({k_good / k_total * 100:.0f} %)",
        "k4": f"{_k4_good(result) / k4_total * 100:.1f} %",
        "k5": "✓" if result.get("k5_perfect") else "✗",
    }


def detail_blocks(result: dict, params: dict) -> list[tuple[str, str]]:
    blocks = [
        ("Dataset k — resultado por imagen",
         _per_image(result.get("k_rows", [])) + "\n\n" +
         _confusion_table(result.get("k_rows", []))),
        (f"Dataset k4 — muestra aleatoria ({result.get('k4_total', 0)} imágenes)",
         _confusion_table(result.get("k4_rows", []), pct=True)),
        ("Dataset k5",
         ("Todas las imágenes A'✓ B'✗ — objetivo cumplido"
          if result.get("k5_perfect") else
          "No se cumple en el 100 % de las imágenes") + "\n\n" +
         _confusion_table(result.get("k5_rows", []))),
    ]
    return blocks


def _per_image(rows: list[dict]) -> str:
    if not rows:
        return "(sin imágenes)"
    lines = []
    for row in rows:
        if row.get("error"):
            lines.append(f"  {row['file']:<22} ERROR: {row['error']}")
            continue
        a = "A'✓" if row["a_accept"] else "A'✗"
        b = "B'✓" if row["b_accept"] else "B'✗"
        lines.append(f"  {row['file']:<22} {a}  {b}")
    return "\n".join(lines)


def _confusion_table(rows: list[dict], pct: bool = False) -> str:
    counts = {(True, True): 0, (True, False): 0, (False, True): 0, (False, False): 0}
    valid = 0
    errors = 0
    for row in rows:
        if row.get("error"):
            errors += 1
            continue
        counts[(row["a_accept"], row["b_accept"])] += 1
        valid += 1

    def cell(a, b):
        n = counts[(a, b)]
        if pct and valid:
            return f"{n / valid * 100:6.1f}%"
        return f"{n:6d}"

    table = (
        "               B'✓      B'✗\n"
        f"  A'✓  |  {cell(True, True)} | {cell(True, False)} |\n"
        f"  A'✗  |  {cell(False, True)} | {cell(False, False)} |"
    )
    if errors:
        table += f"\n\n  {errors} imagen(es) con error, excluidas del conteo."
    return table
