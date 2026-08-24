"""
scoring.py — Ejemplo mínimo. Corre EN EL SERVIDOR.

Contrato:
    SUMMARY_COLUMNS          columnas del leaderboard, en orden
    score(result, params)    -> float
    summary(result, params)  -> dict con esas columnas
    detail_blocks(r, params) -> [(título, texto)]   (opcional)
"""

SUMMARY_COLUMNS = ["Correctos", "Tiempo"]


def score(result: dict, params: dict) -> float:
    """Corrección como base; la rapidez solo desempata."""
    casos = result.get("casos", 0) or 1
    correccion = result.get("aciertos", 0) / casos
    if correccion < 1.0:
        return correccion * 0.9

    segundos = max(result.get("segundos", 0.0), 1e-6)
    bonus = min(0.1, 0.1 / (1 + segundos))
    return 0.9 + bonus


def summary(result: dict, params: dict) -> dict:
    casos = result.get("casos", 0)
    return {
        "Correctos": f"{result.get('aciertos', 0)}/{casos}",
        "Tiempo": f"{result.get('segundos', 0):.3f} s",
    }


def detail_blocks(result: dict, params: dict) -> list[tuple[str, str]]:
    fallos = result.get("fallos", [])
    if not fallos:
        return [("Resultado", "Todos los casos correctos.")]
    lineas = [f"  caso {f['caso']}: {f['error']}" for f in fallos]
    return [("Casos fallidos (primeros 10)", "\n".join(lineas))]
