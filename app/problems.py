"""
problems.py — Descubrimiento y carga de los plugins de problems/.

Un problema es una carpeta autocontenida:

    problems/<slug>/
        manifest.toml     metadatos, límites, datasets, contrato de entrega
        evaluator.py      evaluate(ctx) -> dict   (corre DENTRO del contenedor)
        scoring.py        score/summary/detail    (corre en el servidor)
        template/         archivos de ejemplo para los participantes
        <otros .py>       código propio del problema, copiado al contenedor

Añadir un problema nuevo = crear una carpeta. No se toca nada de app/.
Las carpetas que empiezan por "_" se ignoran (p. ej. _plantilla).
"""

import importlib.util
import logging
import tomllib
from pathlib import Path
from typing import Any

from app.config import PROBLEMS_DIR

log = logging.getLogger(__name__)

# Archivos del problema que NO se copian al contenedor
_NOT_SHIPPED = {"manifest.toml", "scoring.py", "Dockerfile"}
_NOT_SHIPPED_DIRS = {"template", "datasets", "__pycache__"}


class ProblemError(Exception):
    pass


class Problem:
    """Un plugin de problema ya cargado y validado."""

    def __init__(self, directory: Path, manifest: dict):
        self.dir = directory
        self.manifest = manifest
        self.slug: str = manifest["slug"]
        self.title: str = manifest.get("title", self.slug)
        self.description: str = manifest.get("description", "")
        self.enabled_default: bool = bool(manifest.get("enabled", True))
        self.ranking: str = manifest.get("ranking", "last")
        self._scoring = None

        # Estado en caliente, refrescado desde la base de datos
        self.enabled: bool = self.enabled_default
        self.is_open: bool = True

    # ── Secciones del manifest ────────────────────────────────────────────────

    @property
    def submission(self) -> dict:
        return self.manifest.get("submission", {})

    @property
    def files(self) -> list[dict]:
        """Archivos que el equipo debe/puede entregar."""
        return self.submission.get("files", [])

    @property
    def required_files(self) -> list[str]:
        return [f["name"] for f in self.files if f.get("required", False)]

    @property
    def limits(self) -> dict:
        lim = self.manifest.get("limits", {})
        return {
            "timeout_secs": int(lim.get("timeout_secs", 600)),
            "memory": str(lim.get("memory", "2g")),
            "cpus": str(lim.get("cpus", "1")),
            "pids": int(lim.get("pids", 256)),
            "cooldown_mins": float(lim.get("cooldown_mins", 5)),
        }

    @property
    def docker(self) -> dict:
        d = self.manifest.get("docker", {})
        return {
            "base_image": d.get("base_image", "python:3.11-slim"),
            "apt_packages": list(d.get("apt_packages", [])),
            "pip_packages": list(d.get("pip_packages", [])),
        }

    @property
    def params(self) -> dict:
        """Parámetros que llegan al evaluator y al scoring."""
        return dict(self.manifest.get("params", {}))

    @property
    def datasets(self) -> dict[str, Path]:
        """nombre → ruta absoluta en el host (se montan read-only)."""
        out = {}
        for name, raw in self.manifest.get("datasets", {}).items():
            p = Path(raw)
            out[name] = p if p.is_absolute() else (self.dir / p).resolve()
        return out

    @property
    def custom_dockerfile(self) -> Path | None:
        p = self.dir / "Dockerfile"
        return p if p.exists() else None

    @property
    def template_dir(self) -> Path | None:
        p = self.dir / "template"
        return p if p.is_dir() else None

    def shipped_files(self) -> list[Path]:
        """Archivos del problema que se copian al contenedor."""
        out = []
        for item in sorted(self.dir.iterdir()):
            if item.is_dir():
                if item.name not in _NOT_SHIPPED_DIRS and not item.name.startswith("."):
                    out.append(item)
            elif item.name not in _NOT_SHIPPED and not item.name.startswith("."):
                out.append(item)
        return out

    def missing_datasets(self) -> list[str]:
        return [name for name, path in self.datasets.items() if not path.exists()]

    # ── Scoring (se ejecuta en el servidor) ───────────────────────────────────

    @property
    def scoring(self):
        if self._scoring is None:
            path = self.dir / "scoring.py"
            if not path.exists():
                raise ProblemError(f"[{self.slug}] falta scoring.py")
            spec = importlib.util.spec_from_file_location(f"scoring_{self.slug}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._scoring = module
        return self._scoring

    def score(self, result: dict) -> float:
        try:
            return float(self.scoring.score(result, self.params))
        except Exception:
            log.exception("[%s] error calculando score", self.slug)
            return 0.0

    def summary(self, result: dict) -> dict[str, Any]:
        """Columnas del leaderboard: {nombre: valor formateado}."""
        try:
            return dict(self.scoring.summary(result, self.params))
        except Exception:
            log.exception("[%s] error en summary()", self.slug)
            return {}

    def summary_columns(self) -> list[str]:
        return list(getattr(self.scoring, "SUMMARY_COLUMNS", []))

    def detail_blocks(self, result: dict) -> list[tuple[str, str]]:
        """Bloques de texto con el desglose del resultado, para la página del run."""
        fn = getattr(self.scoring, "detail_blocks", None)
        if not fn:
            return []
        try:
            return [(str(t), str(b)) for t, b in fn(result, self.params)]
        except Exception:
            log.exception("[%s] error en detail_blocks()", self.slug)
            return []


# ── Registro ──────────────────────────────────────────────────────────────────

_registry: dict[str, Problem] = {}
_load_errors: dict[str, str] = {}


def _load_one(directory: Path) -> Problem:
    manifest_path = directory / "manifest.toml"
    if not manifest_path.exists():
        raise ProblemError("falta manifest.toml")
    with open(manifest_path, "rb") as f:
        manifest = tomllib.load(f)

    manifest.setdefault("slug", directory.name)
    if manifest["slug"] != directory.name:
        raise ProblemError(
            f"el slug '{manifest['slug']}' no coincide con la carpeta '{directory.name}'"
        )
    if not (directory / "evaluator.py").exists():
        raise ProblemError("falta evaluator.py")
    if not (directory / "scoring.py").exists():
        raise ProblemError("falta scoring.py")

    problem = Problem(directory, manifest)
    problem.scoring  # carga temprana: falla ahora y no a mitad de un run
    if not problem.files:
        raise ProblemError("el manifest no declara [[submission.files]]")
    return problem


def load_all(db=None) -> dict[str, Problem]:
    """Escanea problems/, carga los plugins y sincroniza su estado con la DB."""
    _registry.clear()
    _load_errors.clear()

    if not PROBLEMS_DIR.is_dir():
        log.warning("No existe el directorio %s", PROBLEMS_DIR)
        return _registry

    for directory in sorted(PROBLEMS_DIR.iterdir()):
        if not directory.is_dir() or directory.name.startswith((".", "_")):
            continue
        try:
            problem = _load_one(directory)
        except Exception as e:
            _load_errors[directory.name] = str(e)
            log.error("Problema '%s' no cargado: %s", directory.name, e)
            continue

        _registry[problem.slug] = problem
        if db is not None:
            db.sync_problem(problem.slug, problem.title, problem.enabled_default)
        missing = problem.missing_datasets()
        if missing:
            log.warning(
                "[%s] datasets no encontrados: %s", problem.slug, ", ".join(missing)
            )
        log.info("Problema cargado: %s (%s)", problem.slug, problem.title)

    if db is not None:
        refresh_state(db)
    return _registry


def refresh_state(db):
    """Trae enabled/is_open desde la base de datos al registro en memoria."""
    for state in db.list_problem_states():
        problem = _registry.get(state["slug"])
        if problem:
            problem.enabled = bool(state["enabled"])
            problem.is_open = bool(state["is_open"])


def get(slug: str) -> Problem | None:
    return _registry.get(slug)


def all_problems() -> list[Problem]:
    return sorted(_registry.values(), key=lambda p: p.title.lower())


def active_problems() -> list[Problem]:
    return [p for p in all_problems() if p.enabled]


def load_errors() -> dict[str, str]:
    return dict(_load_errors)
