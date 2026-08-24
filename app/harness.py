"""
harness.py — Punto de entrada DENTRO del contenedor Docker.

Es el único código del sistema que corre en el contenedor. Prepara el contexto,
llama a evaluate(ctx) del problema e imprime el resultado como una sola línea
JSON en stdout.

Todo lo que el código del equipo (o el evaluador) imprima se redirige a stderr,
de forma que stdout contiene exclusivamente el JSON del resultado.

Layout dentro del contenedor:
    /eval/harness.py       este archivo
    /eval/params.json      parámetros del manifest
    /eval/problem/         código del problema (evaluator.py y sus módulos)
    /eval/submission/      archivos entregados por el equipo
    /data/<dataset>        datasets montados read-only
"""

import contextlib
import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path

EVAL_DIR = Path("/eval")
PROBLEM_DIR = EVAL_DIR / "problem"
SUBMISSION_DIR = EVAL_DIR / "submission"


class SubmissionError(Exception):
    """Error atribuible al código entregado por el equipo."""


class Context:
    """Lo que el evaluador del problema recibe."""

    def __init__(self, params: dict, datasets: dict[str, Path], submission_dir: Path):
        self.params = params
        self.datasets = datasets
        self.submission_dir = submission_dir
        self._modules: dict[str, object] = {}

    def dataset(self, name: str) -> Path:
        if name not in self.datasets:
            raise KeyError(f"El dataset '{name}' no está montado en el contenedor")
        return self.datasets[name]

    def load(self, filename: str):
        """Importa un archivo entregado por el equipo y devuelve el módulo."""
        if filename in self._modules:
            return self._modules[filename]
        path = self.submission_dir / filename
        if not path.exists():
            raise SubmissionError(f"No se encontró {filename} en la entrega")
        spec = importlib.util.spec_from_file_location(f"submission_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            raise SubmissionError(f"Error al importar {filename}: {e}") from e
        self._modules[filename] = module
        return module

    def require(self, filename: str, symbol: str):
        """Importa el archivo y devuelve el símbolo exigido por el contrato."""
        module = self.load(filename)
        if not hasattr(module, symbol):
            raise SubmissionError(f"{filename} no define «{symbol}»")
        return getattr(module, symbol)


def main() -> int:
    real_stdout = sys.stdout
    try:
        params = json.loads((EVAL_DIR / "params.json").read_text())
        datasets = {
            name: Path(path)
            for name, path in json.loads(os.environ.get("PROBLEM_DATASETS", "{}")).items()
        }

        sys.path.insert(0, str(PROBLEM_DIR))
        import evaluator  # noqa: E402  (el plugin del problema)

        ctx = Context(params, datasets, SUBMISSION_DIR)

        # Cualquier print del evaluador o del equipo va a stderr.
        with contextlib.redirect_stdout(sys.stderr):
            result = evaluator.evaluate(ctx)

        if not isinstance(result, dict):
            raise TypeError("evaluate(ctx) debe retornar un dict")

        print(json.dumps({"ok": True, "result": result}, default=str), file=real_stdout)
        return 0

    except SubmissionError as e:
        print(json.dumps({"ok": False, "error": str(e), "blame": "submission"}),
              file=real_stdout)
        return 1
    except Exception:
        print(json.dumps({"ok": False, "error": traceback.format_exc()[-2000:],
                          "blame": "evaluator"}), file=real_stdout)
        return 1


if __name__ == "__main__":
    sys.exit(main())
