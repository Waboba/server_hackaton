"""
solution.py — Plantilla de entrega.

REGLAS
  - El archivo debe llamarse exactamente solution.py
  - Debe definir main(image) con esta firma exacta
  - image llega como numpy array uint8 en escala de grises
  - main() debe devolver un numpy array uint8 en escala de grises

DISPONIBLE SIN DECLARAR
  cv2 (opencv-python-headless) y numpy

PAQUETES EXTRA
  Declara hasta 10 paquetes adicionales en requirements.txt.
  Dentro del contenedor NO hay acceso a internet durante la ejecución.
"""

import cv2
import numpy as np


def main(image: np.ndarray) -> np.ndarray:
    """
    Recibe una huella dactilar y devuelve la huella modificada.

    El objetivo es que el algoritmo A' siga aceptándola y B' la rechace.
    """
    # Ejemplo mínimo: suavizado gaussiano leve
    modified = cv2.GaussianBlur(image, (5, 5), 1.0)
    return modified
