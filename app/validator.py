"""
validator.py — Valida una entrega contra el contrato declarado en el manifest
del problema. No sabe nada de ningún problema en concreto.

Contrato (manifest.toml):

    [[submission.files]]
    name = "solution.py"
    required = true
    must_define = "main"        # símbolo que el archivo debe definir (AST)

    [submission]
    max_packages = 10
    blocked_packages = ["requests", "httpx"]
    preinstalled = ["numpy", "opencv-python-headless"]
    strip_packages = ["opencv-python"]
"""

import ast
from pathlib import Path

# Paquetes vetados siempre, en cualquier problema: dan salida de red,
# ejecución remota o control de la máquina anfitriona.
GLOBAL_BLOCKED = {
    "requests", "httpx", "aiohttp", "urllib3", "paramiko", "fabric",
    "sh", "plumbum", "docker", "boto3", "pyautogui", "pynput",
    "scapy", "pexpect", "ftputil",
}

MAX_FILE_BYTES = 2 * 1024 * 1024


def validate_submission(problem, submission_dir: Path) -> tuple[bool, str]:
    """Retorna (ok, mensaje_de_error)."""
    for spec in problem.files:
        name = spec["name"]
        path = submission_dir / name
        if not path.exists():
            if spec.get("required", False):
                return False, f"Falta el archivo obligatorio {name}"
            continue

        if path.stat().st_size > MAX_FILE_BYTES:
            return False, f"{name} supera el tamaño máximo permitido"

        if name.endswith(".py"):
            ok, err = _validate_python(path, spec.get("must_define"))
            if not ok:
                return False, err
        elif name == "requirements.txt" or spec.get("kind") == "requirements":
            ok, err = _validate_requirements(path, problem)
            if not ok:
                return False, err

    return True, ""


def _validate_python(path: Path, must_define: str | None) -> tuple[bool, str]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return False, f"No se pudo leer {path.name}: {e}"

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, f"Error de sintaxis en {path.name} (línea {e.lineno}): {e.msg}"

    if must_define:
        defined = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined.add(target.id)
        if must_define not in defined:
            return False, f"{path.name} debe definir «{must_define}»"

    return True, ""


def parse_requirements(text: str) -> list[tuple[str, str]]:
    """Devuelve [(nombre_normalizado, línea_original)]."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = line
        for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", ";", " "):
            name = name.split(sep)[0]
        name = name.strip().lower().replace("_", "-")
        if name:
            out.append((name, line))
    return out


def _validate_requirements(path: Path, problem) -> tuple[bool, str]:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return True, ""

    sub = problem.submission
    max_packages = int(sub.get("max_packages", 10))
    preinstalled = {p.lower().replace("_", "-") for p in sub.get("preinstalled", [])}
    blocked = GLOBAL_BLOCKED | {
        p.lower().replace("_", "-") for p in sub.get("blocked_packages", [])
    }

    packages = parse_requirements(text)

    hits = sorted({n for n, _ in packages if n in blocked})
    if hits:
        return False, f"Paquetes no permitidos: {', '.join(hits)}"

    for _, line in packages:
        low = line.lower()
        if low.startswith(("git+", "http://", "https://", "-e ", "--", "file:")):
            return False, f"No se permiten URLs ni flags en requirements.txt: {line}"

    extra = [n for n, _ in packages if n not in preinstalled]
    if len(extra) > max_packages:
        return False, (
            f"Máximo {max_packages} paquetes extra (has declarado {len(extra)}). "
            f"Ya vienen instalados: {', '.join(sorted(preinstalled)) or 'ninguno'}"
        )

    return True, ""


def filter_requirements(text: str, problem) -> str:
    """
    Quita del requirements.txt los paquetes que chocan con los ya instalados
    en la imagen base (manifest: submission.strip_packages).
    """
    strip = {p.lower().replace("_", "-") for p in problem.submission.get("strip_packages", [])}
    if not strip:
        return text
    kept = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = parse_requirements(line)
        if name and name[0][0] in strip:
            continue
        kept.append(line)
    return "\n".join(kept) + ("\n" if kept else "")
