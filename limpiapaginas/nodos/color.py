"""Color, niveles y contraste."""
from __future__ import annotations

import cv2
import numpy as np

from ..registro import IMAGEN, Decimal, Entero, Opcion, nodo
from ..util import gris, por_canal


@nodo("color/grises", titulo="A escala de grises", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Opcion("metodo", "luminancia", ["luminancia", "rojo", "verde", "azul", "mínimo", "máximo"],
                     etiqueta="método",
                     ayuda="Un solo canal puede borrar manchas o sellos de color: el rojo aclara las manchas "
                           "rojizas y el papel amarillento; «mínimo» conserva cualquier tinta de color")],
      descripcion="Convierte la página en color a una sola banda de grises.")
def grises(ctx, imagen, metodo):
    if imagen.ndim == 2:
        return imagen
    b, g, r = cv2.split(imagen)
    if metodo == "rojo":
        return r
    if metodo == "verde":
        return g
    if metodo == "azul":
        return b
    if metodo == "mínimo":
        return np.minimum(np.minimum(b, g), r)
    if metodo == "máximo":
        return np.maximum(np.maximum(b, g), r)
    return gris(imagen)


@nodo("color/invertir", titulo="Invertir", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      descripcion="Negativo: lo claro pasa a oscuro. También invierte una máscara.")
def invertir(ctx, imagen):
    return cv2.bitwise_not(imagen)


@nodo("color/niveles", titulo="Niveles", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Entero("negro", 0, 0, 254, ayuda="Todo lo que sea más oscuro pasa a negro puro"),
              Entero("blanco", 255, 1, 255, ayuda="Todo lo que sea más claro pasa a blanco puro"),
              Decimal("gamma", 1.0, 0.1, 5.0, paso=0.05, ayuda="Menor que 1 oscurece los medios tonos; mayor, los aclara")],
      descripcion="Fija el punto negro, el punto blanco y la gamma, como en un editor de fotos.")
def niveles(ctx, imagen, negro, blanco, gamma):
    blanco = max(blanco, negro + 1)
    x = np.arange(256, dtype=np.float32)
    y = np.clip((x - negro) / (blanco - negro), 0, 1) ** (1.0 / gamma)
    return cv2.LUT(imagen, np.rint(y * 255).astype(np.uint8))


@nodo("color/contraste_auto", titulo="Contraste automático", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Decimal("percentil_bajo", 1.0, 0.0, 30.0, paso=0.5, etiqueta="percentil bajo"),
              Decimal("percentil_alto", 99.0, 70.0, 100.0, paso=0.5, etiqueta="percentil alto")],
      descripcion="Estira el histograma: el percentil bajo pasa a negro y el alto a blanco.")
def contraste_auto(ctx, imagen, percentil_bajo, percentil_alto):
    g = gris(imagen)
    paso = max(1, max(g.shape) // 800)
    lo, hi = np.percentile(g[::paso, ::paso], [percentil_bajo, percentil_alto])
    if hi - lo < 1:
        return imagen
    x = np.arange(256, dtype=np.float32)
    lut = np.rint(np.clip((x - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)
    return cv2.LUT(imagen, lut)


@nodo("color/clahe", titulo="Contraste local (CLAHE)", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Decimal("limite", 2.0, 0.5, 20.0, paso=0.1, etiqueta="límite",
                      ayuda="Más alto, más contraste local y también más ruido"),
              Entero("rejilla", 8, 2, 32, ayuda="Número de zonas por lado")],
      descripcion="Realza la tinta desvaída por zonas. En color solo toca la luminosidad.")
def clahe(ctx, imagen, limite, rejilla):
    op = cv2.createCLAHE(clipLimit=float(limite), tileGridSize=(rejilla, rejilla))
    if imagen.ndim == 2:
        return op.apply(imagen)
    lab = cv2.cvtColor(imagen, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = op.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


@nodo("color/nitidez", titulo="Enfocar (máscara de enfoque)", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Decimal("radio", 1.5, 0.3, 20.0, paso=0.1), Decimal("cantidad", 1.0, 0.0, 5.0, paso=0.05)],
      descripcion="Afila los bordes de las letras restando una copia desenfocada.")
def nitidez(ctx, imagen, radio, cantidad):
    suave = cv2.GaussianBlur(imagen, (0, 0), radio)
    return cv2.addWeighted(imagen, 1 + cantidad, suave, -cantidad, 0)
