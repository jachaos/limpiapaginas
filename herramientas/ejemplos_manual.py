"""Genera las imágenes de «antes y después» del manual a partir de las páginas de muestra.

Uso:  python herramientas/ejemplos_manual.py docs/manual
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

from limpiapaginas import motor, nodos  # noqa: F401
from limpiapaginas.config import RAIZ
from limpiapaginas.util import color, escribir_imagen, leer_imagen

destino = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/manual")
ENTRADA = RAIZ / "datos" / "entrada"
F1 = motor.cargar_flujo(RAIZ / "flujos" / "1 Limpieza general.json")
F2 = motor.cargar_flujo(RAIZ / "flujos" / "2 Blanco y negro (Sauvola).json")


def final(flujo, pagina):
    r = motor.ejecutar(flujo, pagina, cache=None)
    guardar = next(n["id"] for n in flujo["nodos"] if n["tipo"] == "salida/guardar")
    return r[guardar].salidas["imagen"]


def rotulo(img, texto):
    img = color(img).copy()
    alto = 46
    banda = np.full((alto, img.shape[1], 3), 40, np.uint8)
    cv2.putText(banda, texto, (14, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([banda, img])


def junto(*paneles, alto=None):
    alto = alto or min(p.shape[0] for p in paneles)
    esc = [cv2.resize(p, (int(p.shape[1] * alto / p.shape[0]), alto), interpolation=cv2.INTER_AREA) for p in paneles]
    sep = np.full((alto, 14, 3), 255, np.uint8)
    fila = []
    for i, e in enumerate(esc):
        fila += [e] + ([sep] if i < len(esc) - 1 else [])
    return np.hstack(fila)


def pagina_entera(nombre, salida, alto=1100):
    p = ENTRADA / nombre
    o, a = leer_imagen(p, "grises"), final(F1, p)
    r = lambda x: cv2.resize(x, (int(x.shape[1] * alto / x.shape[0]), alto), interpolation=cv2.INTER_AREA)  # noqa: E731
    escribir_imagen(destino / salida, junto(rotulo(r(o), "Original"), rotulo(r(a), "Limpia")), 88)
    print("  ", salida)


def detalle(nombre, salida, caja, tres=True):
    p = ENTRADA / nombre
    x, y, w, h = caja
    o = leer_imagen(p, "grises")
    paneles = [rotulo(o[y:y + h, x:x + w], "Original"), rotulo(final(F1, p)[y:y + h, x:x + w], "Limpieza general")]
    if tres:
        paneles.append(rotulo(final(F2, p)[y:y + h, x:x + w], "Blanco y negro"))
    escribir_imagen(destino / salida, junto(*paneles), 90)
    print("  ", salida)


pagina_entera("CL-12-1909_p0007.png", "e1_marco.jpg")
pagina_entera("ejemplo_3.png", "e2_torcida.jpg")
pagina_entera("CL-1911_p0010.png", "e3_recuadro.jpg")
detalle("CL-1911_p0029.png", "e4_detalle_texto.jpg", (300, 700, 900, 520))
detalle("CL-1909-AP2_p0005.png", "e5_detalle_gris.jpg", (120, 300, 700, 420))
