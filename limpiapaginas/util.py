"""Utilidades de imagen compartidas por los nodos."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def leer_imagen(ruta: Path, modo: str = "original") -> np.ndarray:
    """Lee una imagen de disco. Admite rutas con tildes y espacios también en Windows.

    ``modo``: ``original`` (como esté), ``grises`` o ``color`` (BGR).
    """
    datos = np.fromfile(str(ruta), dtype=np.uint8)
    # ANYDEPTH: sin él OpenCV pasa los 16 bits a 8 quedándose con el byte alto, y un escaneo
    # de 12 bits guardado en 16 sale casi negro
    bandera = {"grises": cv2.IMREAD_GRAYSCALE | cv2.IMREAD_ANYDEPTH,
               "color": cv2.IMREAD_COLOR | cv2.IMREAD_ANYDEPTH}.get(modo, cv2.IMREAD_UNCHANGED)
    img = cv2.imdecode(datos, bandera)
    if img is None:
        raise ValueError(f"No se pudo leer la imagen: {ruta}")
    if img.dtype == np.uint16:
        # profundidad real: muchos escáneres guardan 10, 12 o 14 bits en un contenedor de 16
        maximo = int(img.max())
        tope = next(t for t in (255, 1023, 4095, 16383, 65535) if maximo <= t)
        img = np.round(img.astype(np.float32) * (255.0 / tope)).astype(np.uint8)
    elif img.dtype != np.uint8:                  # TIFF en coma flotante u otras rarezas
        img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    if img.ndim == 3 and img.shape[2] == 4:      # con transparencia: se compone sobre blanco
        alfa = img[:, :, 3:4].astype(np.float32) / 255
        img = (img[:, :, :3] * alfa + 255 * (1 - alfa)).astype(np.uint8)
    if img.ndim == 3 and img.shape[2] == 3 and modo == "original":
        b, g, r = cv2.split(img)
        if np.array_equal(b, g) and np.array_equal(g, r):   # "color" que en realidad es gris
            img = g
    return img


def escribir_imagen(ruta: Path, img: np.ndarray, calidad_jpg: int = 92) -> None:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ext = ruta.suffix.lower()
    opciones = []
    if ext in (".jpg", ".jpeg"):
        opciones = [cv2.IMWRITE_JPEG_QUALITY, int(calidad_jpg)]
    elif ext in (".tif", ".tiff"):
        opciones = [cv2.IMWRITE_TIFF_COMPRESSION, 5]   # LZW, sin pérdida
    ok, buf = cv2.imencode(ext, img, opciones)
    if not ok:
        raise ValueError(f"No se pudo codificar la imagen como {ext}")
    buf.tofile(str(ruta))


def gris(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def color(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def impar(n: int) -> int:
    n = max(1, int(n))
    return n if n % 2 else n + 1


def tinta(img: np.ndarray, umbral: int = 0) -> np.ndarray:
    """Máscara de tinta (255 = tinta) de una imagen con texto oscuro sobre papel claro.

    Con ``umbral`` 0 se calcula con Otsu.
    """
    g = gris(img)
    if umbral <= 0:
        _, m = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    else:
        _, m = cv2.threshold(g, umbral, 255, cv2.THRESH_BINARY_INV)
    return m


def nivel_papel(img: np.ndarray, percentil: float = 75) -> float:
    """Gris típico del papel: un percentil alto de la imagen reducida."""
    g = gris(img)
    paso = max(1, max(g.shape) // 600)
    return float(np.percentile(g[::paso, ::paso], percentil))


def por_canal(img: np.ndarray, funcion):
    """Aplica una función de una sola banda a cada canal de una imagen en color."""
    if img.ndim == 2:
        return funcion(img)
    return cv2.merge([funcion(c) for c in cv2.split(img)])
