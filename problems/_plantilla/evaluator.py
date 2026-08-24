"""
evaluator.py — Ejemplo mínimo. Corre DENTRO del contenedor.

Contrato: evaluate(ctx) -> dict serializable a JSON.
"""

import random
import time


def evaluate(ctx) -> dict:
    params = ctx.params
    rng = random.Random(int(params.get("semilla", 12345)))

    # ctx.require importa la entrega y comprueba el símbolo del contrato.
    # Si el archivo no existe o no define main(), el equipo verá un error claro.
    team_fn = ctx.require("solution.py", "main")

    casos = int(params.get("casos", 200))
    tamano_maximo = int(params.get("tamano_maximo", 2000))

    aciertos = 0
    fallos = []
    total_secs = 0.0

    for i in range(casos):
        entrada = [rng.randint(-10**6, 10**6)
                   for _ in range(rng.randint(0, tamano_maximo))]
        esperado = sorted(entrada)

        inicio = time.perf_counter()
        try:
            obtenido = team_fn(list(entrada))
        except Exception as e:
            fallos.append({"caso": i, "error": str(e)[:120]})
            continue
        total_secs += time.perf_counter() - inicio

        if obtenido == esperado:
            aciertos += 1
        elif len(fallos) < 10:
            fallos.append({"caso": i, "error": "resultado incorrecto",
                           "tamano": len(entrada)})

    return {
        "casos": casos,
        "aciertos": aciertos,
        "fallos": fallos,
        "segundos": round(total_secs, 4),
    }
