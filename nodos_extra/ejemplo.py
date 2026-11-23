"""Plantilla para añadir procesos propios.

Cada fichero ``.py`` de esta carpeta se carga al arrancar (o al pulsar
«Recargar nodos» en la interfaz). Una función decorada con ``@nodo`` aparece
en la paleta con sus controles, sin tocar nada más.

Reglas:

* El primer argumento es ``ctx``; después va un argumento por cada entrada y
  uno por cada parámetro, con los mismos nombres que en el decorador.
* Las imágenes son ``numpy`` uint8: HxW en grises o HxWx3 en color (orden BGR,
  como en OpenCV). Papel claro, tinta oscura.
* Si hay una sola salida se devuelve el valor; si hay varias, una tupla en el
  mismo orden que ``salidas``.
* El primer tramo del tipo ("mios/…") es la categoría de la paleta.

Parámetros disponibles: ``Entero``, ``Decimal``, ``Opcion``, ``Logico`` y ``Texto``.
"""
import cv2
import numpy as np

from limpiapaginas.registro import IMAGEN, Entero, Opcion, nodo
from limpiapaginas.util import nivel_papel


@nodo("mios/quitar_color", titulo="Quitar tinta de color",
      entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN, "mascara": IMAGEN},
      params=[Entero("saturacion", 70, 0, 255, etiqueta="saturación mínima",
                     ayuda="Los píxeles más saturados que esto se consideran tinta de color"),
              Entero("margen", 2, 0, 20, ayuda="Píxeles que se agranda la zona borrada"),
              Opcion("relleno", "papel", ["papel", "blanco"])],
      descripcion="Borra sellos, subrayados y anotaciones en tinta de color (rojo, azul, violeta) y deja "
                  "la tinta negra. Necesita la página en color. Ejemplo de nodo propio: está en "
                  "nodos_extra/ejemplo.py.")
def quitar_color(ctx, imagen, saturacion, margen, relleno):
    if imagen.ndim == 2:
        ctx.avisar("La imagen está en grises: no hay color que quitar")
        return imagen, np.zeros(imagen.shape, np.uint8)
    hsv = cv2.cvtColor(imagen, cv2.COLOR_BGR2HSV)
    mascara = ((hsv[:, :, 1] > saturacion) & (hsv[:, :, 2] > 40)).astype(np.uint8) * 255
    if margen:
        mascara = cv2.dilate(mascara, np.ones((2 * margen + 1, 2 * margen + 1), np.uint8))
    salida = imagen.copy()
    salida[mascara > 0] = 255 if relleno == "blanco" else int(round(nivel_papel(imagen)))
    return salida, mascara
