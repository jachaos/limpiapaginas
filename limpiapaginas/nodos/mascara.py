"""Máscaras y mezcla de imágenes. En una máscara, blanco = zona seleccionada."""
from __future__ import annotations

import cv2
import numpy as np

from ..registro import IMAGEN, Decimal, Entero, Logico, Opcion, nodo
from ..util import color, gris, nivel_papel


def _igualar(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Adapta ``b`` al tamaño y número de canales de ``a``."""
    if b.shape[:2] != a.shape[:2]:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_LINEAR)
    if a.ndim == 3 and b.ndim == 2:
        b = color(b)
    elif a.ndim == 2 and b.ndim == 3:
        b = gris(b)
    return b


@nodo("mascara/aplicar", titulo="Aplicar máscara", entradas={"imagen": IMAGEN, "mascara": IMAGEN},
      salidas={"imagen": IMAGEN},
      params=[Opcion("fuera", "blanco", ["blanco", "papel", "negro"], ayuda="Qué se pone fuera de la máscara"),
              Entero("margen", 8, 0, 300, ayuda="Agranda la máscara estos píxeles antes de aplicarla"),
              Logico("invertir", False, ayuda="Limpia dentro de la máscara en vez de fuera")],
      descripcion="Conserva la imagen solo donde la máscara es blanca. Con la máscara de «¿Hay texto?» "
                  "borra todo lo que no es texto.")
def aplicar(ctx, imagen, mascara, fuera, margen, invertir):
    m = gris(_igualar(imagen, mascara)) > 127
    if margen > 0:
        m = cv2.dilate(m.astype(np.uint8),
                       cv2.getStructuringElement(cv2.MORPH_RECT, (2 * margen + 1, 2 * margen + 1))) > 0
    if invertir:
        m = ~m
    valor = {"blanco": 255, "negro": 0}.get(fuera)
    if valor is None:
        valor = int(round(nivel_papel(imagen)))
    salida = imagen.copy()
    salida[~m] = valor
    return salida


@nodo("mascara/combinar", titulo="Combinar imágenes", entradas={"a": IMAGEN, "b": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Opcion("operacion", "mínimo", ["mínimo", "máximo", "mezcla", "diferencia", "y", "o", "xor"],
                     etiqueta="operación",
                     ayuda="«mínimo» se queda con la tinta de las dos; «máximo», solo con la común"),
              Decimal("alfa", 0.5, 0.0, 1.0, paso=0.05, ayuda="Solo en «mezcla»: peso de A")],
      descripcion="Une dos ramas del flujo píxel a píxel. Si los tamaños difieren, B se adapta a A.")
def combinar(ctx, a, b, operacion, alfa):
    b = _igualar(a, b)
    if operacion == "mínimo":
        return cv2.min(a, b)
    if operacion == "máximo":
        return cv2.max(a, b)
    if operacion == "mezcla":
        return cv2.addWeighted(a, alfa, b, 1 - alfa, 0)
    if operacion == "diferencia":
        return cv2.absdiff(a, b)
    if operacion == "y":
        return cv2.bitwise_and(a, b)
    if operacion == "o":
        return cv2.bitwise_or(a, b)
    return cv2.bitwise_xor(a, b)
