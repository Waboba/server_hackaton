# Plantilla para crear un problema nuevo

Copia esta carpeta con el nombre del problema y edita lo que necesites:

```bash
cp -r problems/_plantilla problems/mi_problema
```

Después, en el panel de administración → **Problemas** → *Recargar problemas*.
No hace falta reiniciar el servidor ni tocar nada de `app/`.

> Las carpetas que empiezan por `_` se ignoran, por eso esta plantilla no
> aparece en el panel.

## Qué contiene una carpeta de problema

| Archivo | Dónde corre | Para qué |
|---|---|---|
| `manifest.toml` | servidor | Metadatos, límites, datasets, imagen Docker, contrato de entrega y parámetros |
| `evaluator.py` | **contenedor** | `evaluate(ctx) -> dict`: ejecuta el código del equipo y mide |
| `scoring.py` | servidor | `score()`, `summary()` y `detail_blocks()`: puntuación y presentación |
| `template/` | — | Archivos de ejemplo que los participantes se pueden descargar |
| `Dockerfile` | — | Opcional. Si no está, se genera desde `[docker]` del manifest |
| cualquier `.py` | **contenedor** | Se copia junto a `evaluator.py` (p. ej. `matchers.py` en fingerprint) |

## El contexto que recibe `evaluate(ctx)`

```python
ctx.params                        # la sección [params] del manifest
ctx.dataset("nombre")             # Path al dataset montado read-only
ctx.load("solution.py")           # importa un archivo de la entrega
ctx.require("solution.py", "main")# lo importa y devuelve el símbolo exigido
```

Lo que devuelvas debe ser serializable a JSON: se guarda tal cual y es lo que
recibirán `score()` y `summary()`.

## Reglas del entorno de ejecución

- El contenedor **no tiene red** (`--network none`).
- Los datasets se montan en `/data/<nombre>` en modo solo lectura.
- Todo lo que se imprima por stdout se redirige a stderr; el resultado se
  comunica únicamente devolviéndolo desde `evaluate()`.
- Si el código del equipo es el culpable de un fallo, lanza
  `SubmissionError` (está disponible en `harness`) o simplemente deja que la
  excepción suba: el equipo verá el error en la página de su run.

## Dockerfile propio

Solo si `[docker]` no te basta. Debe respetar el layout que espera el sistema:

```dockerfile
FROM tu-imagen
WORKDIR /eval
COPY harness.py params.json ./
COPY problem/ ./problem/
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt || true
COPY submission/ ./submission/
ENTRYPOINT ["python", "-u", "harness.py"]
```
