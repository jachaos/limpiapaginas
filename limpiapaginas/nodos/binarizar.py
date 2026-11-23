"""Binarización: deja la página en blanco y negro puros (tinta negra, papel blanco)."""
from __future__ import annotations

import cv2
import numpy as np

from .. import dispositivo
from ..registro import IMAGEN, Decimal, Entero, Opcion, nodo
from ..util import gris


@nodo("binarizar/umbral", titulo="Umbral fijo", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Entero("umbral", 128, 0, 255)],
      descripcion="Todo lo más oscuro que el umbral pasa a negro. Solo sirve con fondo ya uniforme.")
def umbral(ctx, imagen, umbral):
    _, b = cv2.threshold(gris(imagen), umbral, 255, cv2.THRESH_BINARY)
    return b


@nodo("binarizar/otsu", titulo="Umbral automático (Otsu)", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Entero("ajuste", 0, -100, 100, ayuda="Se suma al umbral calculado: positivo engorda la tinta")],
      descripcion="Busca el umbral que mejor separa tinta y papel para toda la página. Va bien después "
                  "de «Aplanar fondo».")
def otsu(ctx, imagen, ajuste):
    g = gris(imagen)
    t, b = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    if ajuste:
        _, b = cv2.threshold(g, float(np.clip(t + ajuste, 0, 255)), 255, cv2.THRESH_BINARY)
    return b


@nodo("binarizar/adaptativa", titulo="Umbral adaptativo", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Opcion("metodo", "gaussiana", ["gaussiana", "media"], etiqueta="método"),
              Entero("bloque", 35, 3, 301, impar=True, ayuda="Tamaño de la vecindad; unas 2 o 3 veces el alto de letra"),
              Entero("c", 12, -50, 80, ayuda="Cuánto más oscuro que su vecindad debe ser un píxel para ser tinta")],
      descripcion="Umbral distinto en cada zona según su vecindad. Aguanta sombras y manchas, pero puede "
                  "sacar ruido en zonas vacías si «c» es bajo.")
def adaptativa(ctx, imagen, metodo, bloque, c):
    tipo = cv2.ADAPTIVE_THRESH_GAUSSIAN_C if metodo == "gaussiana" else cv2.ADAPTIVE_THRESH_MEAN_C
    return cv2.adaptiveThreshold(gris(imagen), 255, tipo, cv2.THRESH_BINARY, bloque, c)


def _sauvola_cpu(g: np.ndarray, ventana: int, k: float, r: float) -> np.ndarray:
    f = g.astype(np.float32)
    media = cv2.boxFilter(f, cv2.CV_32F, (ventana, ventana), borderType=cv2.BORDER_REFLECT)
    media2 = cv2.sqrBoxFilter(f, cv2.CV_32F, (ventana, ventana), borderType=cv2.BORDER_REFLECT)
    desv = np.sqrt(np.maximum(media2 - media * media, 0))
    umbral = media * (1.0 + k * (desv / r - 1.0))
    return np.where(f > umbral, 255, 0).astype(np.uint8)


def _sauvola_gpu(g: np.ndarray, ventana: int, k: float, r: float) -> np.ndarray:
    import torch
    import torch.nn.functional as F
    x = dispositivo.a_tensor(g)
    p = ventana // 2
    xp = F.pad(x, (p, p, p, p), mode="reflect")
    media = F.avg_pool2d(xp, ventana, stride=1)
    media2 = F.avg_pool2d(xp * xp, ventana, stride=1)
    desv = torch.sqrt((media2 - media * media).clamp(min=0))
    umbral = media * (1.0 + k * (desv / r - 1.0))
    return dispositivo.a_imagen((x > umbral).float() * 255.0)


_METODOS_LOCALES = {"niblack": 0, "wolf": 2, "nick": 3}   # cv2.ximgproc.BINARIZATION_*


@nodo("binarizar/local", titulo="Binarización local (Sauvola…)", entradas={"imagen": IMAGEN},
      salidas={"imagen": IMAGEN}, gpu=True,
      params=[Opcion("metodo", "sauvola", ["sauvola", "wolf", "nick", "niblack"], etiqueta="método",
                     ayuda="Sauvola es la referencia para documentos antiguos; Wolf aguanta mejor el bajo "
                           "contraste; NICK, las páginas muy claras"),
              Entero("ventana", 41, 5, 301, impar=True, ayuda="Unas 2 o 3 veces el alto de una letra"),
              Decimal("k", 0.2, -1.0, 1.0, paso=0.01,
                      ayuda="Sauvola y Wolf: 0,1 a 0,5 (más alto, tinta más fina). NICK y Niblack: -0,2 a -0,1"),
              Entero("r", 128, 1, 255, ayuda="Rango dinámico de la desviación; 128 para imágenes de 8 bits")],
      descripcion="Umbral por zonas que tiene en cuenta el contraste local. Es lo que mejor funciona con "
                  "papel manchado, tinta desigual y texto transparentado. Solo Sauvola tiene versión GPU.")
def local(ctx, imagen, metodo, ventana, k, r):
    g = gris(imagen)
    if metodo == "sauvola":
        return dispositivo.con_gpu(ctx, lambda: _sauvola_gpu(g, ventana, k, float(r)),
                                   lambda: _sauvola_cpu(g, ventana, k, float(r)))
    if not hasattr(cv2, "ximgproc"):
        raise RuntimeError("Este método necesita opencv-contrib-python (módulo ximgproc)")
    return cv2.ximgproc.niBlackThreshold(g, 255, cv2.THRESH_BINARY, ventana, k,
                                         binarizationMethod=_METODOS_LOCALES[metodo], r=float(r))
