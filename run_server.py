#!/usr/bin/env python3
"""
run_server.py — Punto de entrada del servidor de la hackathon.

    python3 run_server.py

Requisitos: Python 3.11 o superior y Docker. Sin dependencias de pip.
"""

import logging
import sys

MIN_PYTHON = (3, 11)


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        print(f"Se requiere Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} o superior "
              f"(tienes {sys.version.split()[0]})", file=sys.stderr)
        return 1

    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
        datefmt="%H:%M:%S",
    )
    # El log de acceso HTTP es ruidoso; solo se ve en modo depuración.
    logging.getLogger("app.web.http").setLevel(logging.INFO)

    from app.web.server import run
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
