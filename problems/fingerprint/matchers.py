"""
matchers.py — Algoritmos A' y B'.

Cada uno es una combinación lineal ponderada de SIFT, ORB y FFT:

    score_A' = w_A[0]*s_SIFT + w_A[1]*s_ORB + w_A[2]*s_FFT
    score_B' = w_B[0]*s_SIFT + w_B[1]*s_ORB + w_B[2]*s_FFT

A' acepta si score_A' > threshold_a
B' acepta si score_B' > threshold_b

La lógica es idéntica a la versión original del bot; lo único que cambia es que
los parámetros llegan desde el manifest en lugar de un config.py global.
"""

from pathlib import Path

import cv2
import numpy as np


# ── Normalización sigmoid (conteo de matches → 0-100) ────────────────────────

def _normalize(score: float, midpoint: float = 40, steepness: float = 0.1) -> float:
    return 100.0 / (1 + np.exp(-steepness * (score - midpoint)))


# ── Carga de imagen ──────────────────────────────────────────────────────────

def load_image(path: str | Path) -> np.ndarray | None:
    try:
        raw = np.fromfile(str(path), np.uint8)
        img = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        img = cv2.equalizeHist(img)
        return cv2.GaussianBlur(img, (3, 3), 0)
    except Exception as e:
        print(f"  [load_image] Error en {path}: {e}")
        return None


# ── Scores individuales ──────────────────────────────────────────────────────

def _sift_score(des1, des2, ratio: float) -> float:
    """Matches buenos entre dos sets de descriptores SIFT (ratio test de Lowe)."""
    if des1 is None or des2 is None:
        return 0.0
    flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=50))
    matches = flann.knnMatch(des1, des2, k=2)
    good = [m for m, n in matches if m.distance < ratio * n.distance]
    return float(len(good))


def _orb_score(des1, des2, ratio: float) -> float:
    """Matches buenos entre dos sets de descriptores ORB (distancia Hamming)."""
    if des1 is None or des2 is None:
        return 0.0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    matches = bf.knnMatch(des1, des2, k=2)
    good = []
    for pair in matches:
        if len(pair) == 2:
            m, n = pair
            if m.distance < ratio * n.distance:
                good.append(m)
    return float(len(good))


def _fft_score(img1: np.ndarray, img2: np.ndarray, target_size) -> float:
    """Correlación cruzada de los espectros FFT."""
    size = tuple(target_size)
    img1_r = cv2.resize(img1, size)
    img2_r = cv2.resize(img2, size)

    dft1 = np.fft.fftshift(np.fft.fft2(img1_r.astype(np.float32)))
    dft2 = np.fft.fftshift(np.fft.fft2(img2_r.astype(np.float32)))

    spec1 = 20 * np.log(np.abs(dft1) + 1)
    spec2 = 20 * np.log(np.abs(dft2) + 1)

    res = cv2.matchTemplate(spec1.astype(np.float32), spec2.astype(np.float32),
                            cv2.TM_CCOEFF_NORMED)
    _, score, _, _ = cv2.minMaxLoc(res)
    return float(score)


# ── Matcher combinado ────────────────────────────────────────────────────────

class CombinedMatcher:
    """A' y B' con una sola instancia: solo difieren en pesos y umbral."""

    def __init__(self, params: dict):
        self.weights_a = list(params.get("weights_a", [0.4, 0.6, 0.0]))
        self.weights_b = list(params.get("weights_b", [0.1, 0.6, 0.3]))
        self.threshold_a = float(params.get("threshold_a", 70))
        self.threshold_b = float(params.get("threshold_b", 70))
        self.sift_ratio = float(params.get("sift_ratio", 0.7))
        self.orb_ratio = float(params.get("orb_ratio", 0.8))
        self.fft_target_size = list(params.get("fft_target_size", [256, 256]))

        self.sift = cv2.SIFT_create(nfeatures=int(params.get("sift_features", 200)))
        self.orb = cv2.ORB_create(nfeatures=int(params.get("orb_features", 1000)))

    def _descriptors(self, img: np.ndarray):
        _, des_sift = self.sift.detectAndCompute(img, None)
        _, des_orb = self.orb.detectAndCompute(img, None)
        return des_sift, des_orb

    def combined_scores(self, img1: np.ndarray, img2: np.ndarray) -> tuple[float, float]:
        ds1, do1 = self._descriptors(img1)
        ds2, do2 = self._descriptors(img2)

        s_sift = _normalize(_sift_score(ds1, ds2, self.sift_ratio))
        s_orb = _normalize(_orb_score(do1, do2, self.orb_ratio))
        s_fft = _fft_score(img1, img2, self.fft_target_size)

        wa, wb = self.weights_a, self.weights_b
        score_a = wa[0] * s_sift + wa[1] * s_orb + wa[2] * s_fft
        score_b = wb[0] * s_sift + wb[1] * s_orb + wb[2] * s_fft
        return score_a, score_b

    def accepts_A(self, score: float) -> bool:
        return bool(score > self.threshold_a)

    def accepts_B(self, score: float) -> bool:
        return bool(score > self.threshold_b)


def evaluate_image(matcher: CombinedMatcher, modified_img: np.ndarray,
                   original_img: np.ndarray) -> tuple[bool, bool]:
    """Compara la imagen modificada con la original. Retorna (acepta_A, acepta_B)."""
    score_a, score_b = matcher.combined_scores(modified_img, original_img)
    return matcher.accepts_A(score_a), matcher.accepts_B(score_b)
