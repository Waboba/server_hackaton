"""
scoring.py — Puntuación por tiempo de ejecución. Corre EN EL SERVIDOR.

El leaderboard de la plataforma ordena por score DESCENDENTE, así que el score
es el tiempo total EN NEGATIVO: -1.2340 significa 1.2340 s, y el más rápido
queda arriba. La columna «Tiempo» muestra los segundos en positivo.

Un run que falla o que excede el umbral no puntúa: recibe PENALIZACION, un
valor tan bajo que siempre queda al final del ranking. Como el problema usa
ranking = "best", ese run nunca desplaza a un run válido anterior del equipo.

Este archivo es idéntico en los seis problemas de tipo «tiempo»: trabaja sobre
el esquema de resultado que produce medicion.correr().
"""

PENALIZACION = -999999.0

SUMMARY_COLUMNS = ["Tiempo", "Instancias", "Estado"]


def score(result: dict, params: dict) -> float:
    if result.get("estado") != "ok":
        return PENALIZACION
    if result.get("completadas", 0) < result.get("total_instancias", 0):
        return PENALIZACION
    return -float(result.get("segundos_total", 0.0))


def summary(result: dict, params: dict) -> dict:
    estado = result.get("estado", "error")
    etiqueta = {
        "ok": "✓",
        "excedido": f"✗ {result.get('umbral_factor', 3)}× excedido",
        "error": "✗ error",
    }.get(estado, "✗")
    return {
        "Tiempo": f"{result.get('segundos_total', 0.0):.3f} s",
        "Instancias": f"{result.get('completadas', 0)}/{result.get('total_instancias', 0)}",
        "Estado": etiqueta,
    }


def detail_blocks(result: dict, params: dict) -> list[tuple[str, str]]:
    bloques = [("Tiempos por instancia", _tabla(result))]

    estado = result.get("estado")
    factor = result.get("umbral_factor", 3.0)
    fallida = result.get("instancia_fallida")

    if estado == "excedido":
        fila = _fila(result, fallida)
        bloques.append((
            "⚠ Umbral de tiempo excedido",
            f"La instancia «{fallida}» superó {factor:g}× el tiempo objetivo\n"
            f"({fila.get('limite_secs', 0):.3f} s de límite) y fue interrumpida.\n\n"
            "Las instancias siguientes no se ejecutaron y el run no puntúa.\n"
            "Optimiza tu implementación y vuelve a entregar.",
        ))
    elif estado == "error":
        fila = _fila(result, fallida)
        bloques.append((
            "⚠ Error en tu código",
            f"La instancia «{fallida}» lanzó una excepción:\n\n"
            f"  {fila.get('error') or '(sin detalle)'}\n\n"
            "Las instancias siguientes no se ejecutaron y el run no puntúa.",
        ))
    else:
        bloques.append((
            "Score",
            f"Tiempo total: {result.get('segundos_total', 0.0):.4f} s\n"
            f"Score = -tiempo_total = {-result.get('segundos_total', 0.0):.4f}\n\n"
            "El leaderboard ordena de mayor a menor, así que menos tiempo = mejor\n"
            "puesto. Solo se mide tiempo: la correctitud del algoritmo se revisa\n"
            "aparte, en el informe del laboratorio.",
        ))

    return bloques


def _fila(result: dict, nombre) -> dict:
    for fila in result.get("instancias", []):
        if fila.get("nombre") == nombre:
            return fila
    return {}


def _tabla(result: dict) -> str:
    filas = result.get("instancias", [])
    if not filas:
        return "(sin instancias)"

    lineas = [
        f"  {'instancia':<10} {'tiempo':>10} {'objetivo':>10} {'límite 3×':>10}  estado",
        f"  {'-' * 10} {'-' * 10:>10} {'-' * 10:>10} {'-' * 10:>10}  {'-' * 8}",
    ]
    for f in filas:
        objetivo = f.get("objetivo_secs", 0.0)
        limite = f.get("limite_secs", 0.0)
        lineas.append(
            f"  {f.get('nombre', '?'):<10} {f.get('segundos', 0.0):>9.4f}s "
            f"{(f'{objetivo:.3f}s' if objetivo > 0 else '—'):>10} "
            f"{(f'{limite:.3f}s' if limite > 0 else '—'):>10}  "
            f"{f.get('estado', '?')}"
        )
        if f.get("descripcion"):
            lineas.append(f"             {f['descripcion']}")
        if f.get("salida"):
            lineas.append(f"             devolvió: {f['salida']}")
        if f.get("error"):
            lineas.append(f"             error: {f['error']}")

    no_corridas = result.get("total_instancias", 0) - len(filas)
    if no_corridas > 0:
        lineas.append(f"\n  {no_corridas} instancia(s) no se ejecutaron.")
    return "\n".join(lineas)
