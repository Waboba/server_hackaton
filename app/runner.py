"""
runner.py — Construye y ejecuta el contenedor Docker de una entrega.

Es genérico: no sabe qué evalúa el problema, solo respeta el contrato.

Flujo (el mismo de siempre, ahora parametrizado por el manifest):
  1. Arma un build context temporal con harness + código del problema + entrega
  2. docker build
  3. docker run  — sin red, con límites de memoria/CPU/PIDs y datasets read-only
  4. Parsea la última línea de stdout como JSON y devuelve el resultado
"""

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import BUILD_TIMEOUT_SECS, KEEP_IMAGES
from app.validator import filter_requirements

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
HARNESS = APP_DIR / "harness.py"

_TAG_SAFE = re.compile(r"[^a-z0-9_.-]+")


class RunnerError(RuntimeError):
    """Fallo de infraestructura o del código del equipo durante la evaluación."""

    def __init__(self, message: str, blame: str = "system"):
        super().__init__(message)
        self.blame = blame  # system | submission | evaluator


@dataclass
class RunOutcome:
    result: dict
    log: str = ""
    duration_secs: float = 0.0
    phases: dict = field(default_factory=dict)


def docker_available() -> tuple[bool, str]:
    try:
        proc = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                              capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return False, "el comando 'docker' no está instalado"
    except subprocess.TimeoutExpired:
        return False, "docker no respondió"
    if proc.returncode != 0:
        return False, (proc.stderr or "").strip()[-200:] or "docker no está disponible"
    return True, proc.stdout.strip()


def image_tag(problem, team_id: int) -> str:
    return _TAG_SAFE.sub("-", f"hack-{problem.slug}-t{team_id}".lower())


# ── Build context ─────────────────────────────────────────────────────────────


def _generate_dockerfile(problem) -> str:
    """Genera el Dockerfile a partir de la sección [docker] del manifest."""
    d = problem.docker
    lines = [f"FROM {d['base_image']}"]

    if d["apt_packages"]:
        pkgs = " ".join(d["apt_packages"])
        lines += [
            "RUN apt-get update && \\",
            f"    apt-get install -y --no-install-recommends {pkgs} && \\",
            "    rm -rf /var/lib/apt/lists/*",
        ]

    if d["pip_packages"]:
        lines.append(
            "RUN pip install --no-cache-dir " + " ".join(d["pip_packages"])
        )

    lines += [
        "WORKDIR /eval",
        # Capas cacheables: harness y código del problema cambian poco
        "COPY harness.py params.json ./",
        "COPY problem/ ./problem/",
        # Dependencias del equipo
        "COPY requirements.txt ./",
        "RUN pip install --no-cache-dir -r requirements.txt || true",
        # La entrega va al final para aprovechar la caché de las capas anteriores
        "COPY submission/ ./submission/",
        'ENTRYPOINT ["python", "-u", "harness.py"]',
    ]
    return "\n".join(lines) + "\n"


def _prepare_context(problem, submission_dir: Path, tmp: Path):
    """Copia todo lo necesario al directorio de build."""
    dockerfile = problem.custom_dockerfile
    if dockerfile:
        shutil.copy(dockerfile, tmp / "Dockerfile")
    else:
        # newline="\n" a propósito: en Windows write_text pondría CRLF y las
        # continuaciones de línea (\) del Dockerfile dejan de funcionar.
        (tmp / "Dockerfile").write_text(_generate_dockerfile(problem),
                                        newline="\n")

    shutil.copy(HARNESS, tmp / "harness.py")
    (tmp / "params.json").write_text(json.dumps(problem.params, indent=2))

    problem_dst = tmp / "problem"
    problem_dst.mkdir()
    for item in problem.shipped_files():
        if item.is_dir():
            shutil.copytree(item, problem_dst / item.name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy(item, problem_dst / item.name)

    submission_dst = tmp / "submission"
    submission_dst.mkdir()
    declared = {f["name"] for f in problem.files}
    requirements_text = ""
    for name in declared:
        src = submission_dir / name
        if not src.exists():
            continue
        if name == "requirements.txt":
            requirements_text = src.read_text(encoding="utf-8", errors="replace")
            continue
        shutil.copy(src, submission_dst / name)

    (tmp / "requirements.txt").write_text(
        filter_requirements(requirements_text, problem), newline="\n"
    )


def build_image(problem, submission_dir: Path, tag: str, label: str = "") -> str:
    """Construye la imagen del equipo. Devuelve el log del build."""
    with tempfile.TemporaryDirectory(prefix="hackbuild-") as tmpdir:
        tmp = Path(tmpdir)
        _prepare_context(problem, submission_dir, tmp)

        try:
            proc = subprocess.run(
                ["docker", "build", "-t", tag, "-f", "Dockerfile", "."],
                cwd=tmp, capture_output=True, text=True, timeout=BUILD_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired:
            raise RunnerError(
                f"El build de Docker superó {BUILD_TIMEOUT_SECS}s. "
                "Revisa tus dependencias en requirements.txt."
            )

        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            log.error("[%s] build falló:\n%s", label or tag, output[-1500:])
            raise RunnerError(
                "Falló la construcción de la imagen Docker "
                f"(normalmente es un paquete de requirements.txt):\n{output[-1200:]}",
                blame="submission",
            )
        return output[-4000:]


# ── Ejecución ─────────────────────────────────────────────────────────────────


def _mount_source(path: Path) -> str:
    """
    Ruta de host tal como la quiere `docker run -v`.

    En Windows hay que dar 'C:/datos/k' y no 'C:\\datos\\k': Docker Desktop
    acepta la barra normal, pero con la invertida trocea mal el argumento.
    Además el disco tiene que estar compartido en Settings → Resources →
    File sharing, o el contenedor verá el directorio vacío.
    """
    return str(path).replace("\\", "/")


def run_container(problem, tag: str, label: str = "") -> tuple[dict, str]:
    limits = problem.limits
    datasets = problem.datasets

    missing = problem.missing_datasets()
    if missing:
        raise RunnerError(
            f"Datasets no encontrados en el servidor: {', '.join(missing)}. "
            "Es un problema de configuración, avisa al organizador."
        )

    cmd = [
        "docker", "run", "--rm",
        "--network", "none",
        "--memory", limits["memory"],
        "--cpus", limits["cpus"],
        "--pids-limit", str(limits["pids"]),
        "--security-opt", "no-new-privileges",
    ]

    mounted = {}
    for name, host_path in datasets.items():
        container_path = f"/data/{name}"
        cmd += ["-v", f"{_mount_source(host_path)}:{container_path}:ro"]
        mounted[name] = container_path
        # Compatibilidad: también como variable de entorno individual
        cmd += ["-e", f"DATASET_{name.upper()}={container_path}"]

    cmd += ["-e", f"PROBLEM_DATASETS={json.dumps(mounted)}", tag]

    hard_timeout = limits["timeout_secs"] + 60
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=hard_timeout)
    except subprocess.TimeoutExpired:
        raise RunnerError(
            f"Timeout: la evaluación superó {hard_timeout}s y fue abortada.",
            blame="submission",
        )

    container_log = (proc.stderr or "").strip()
    if container_log:
        for line in container_log.splitlines()[-15:]:
            log.info("[%s] container: %s", label or tag, line)

    stdout = (proc.stdout or "").strip()
    if not stdout:
        raise RunnerError(
            "El contenedor no produjo ningún resultado.\n"
            f"Salida de error:\n{container_log[-800:] or '(vacía)'}"
        )

    try:
        data = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        raise RunnerError(
            "La salida del contenedor no es JSON válido:\n"
            f"{stdout.splitlines()[-1][:400]}"
        )

    if not data.get("ok"):
        raise RunnerError(
            data.get("error", "error desconocido en el contenedor"),
            blame=data.get("blame", "evaluator"),
        )

    return data["result"], container_log[-4000:]


def remove_image(tag: str):
    subprocess.run(["docker", "rmi", "-f", tag],
                   capture_output=True, text=True, timeout=60)


def run_submission(problem, submission_dir: Path, team_id: int,
                   label: str = "", on_phase=None) -> RunOutcome:
    """
    Evaluación completa de una entrega. `on_phase(fase)` se llama en cada
    cambio de fase para que la web muestre el progreso en vivo.
    """
    def phase(name: str):
        log.info("[%s] fase: %s", label, name)
        if on_phase:
            try:
                on_phase(name)
            except Exception:
                log.exception("callback de fase falló")

    tag = image_tag(problem, team_id)
    started = time.time()
    timings: dict[str, float] = {}

    phase("building")
    t0 = time.time()
    build_log = build_image(problem, submission_dir, tag, label)
    timings["build"] = round(time.time() - t0, 1)

    try:
        phase("running")
        t0 = time.time()
        result, container_log = run_container(problem, tag, label)
        timings["run"] = round(time.time() - t0, 1)
    finally:
        if not KEEP_IMAGES:
            remove_image(tag)

    phase("scoring")
    full_log = (
        f"── docker build ──\n{build_log[-2000:]}\n\n"
        f"── contenedor (stderr) ──\n{container_log}"
    )
    return RunOutcome(
        result=result,
        log=full_log,
        duration_secs=round(time.time() - started, 1),
        phases=timings,
    )
