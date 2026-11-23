"""Detección de texto: ¿hay texto en la página y dónde?"""
from __future__ import annotations

import cv2
import numpy as np

from ..registro import DATOS, IMAGEN, Entero, Decimal, nodo
from ..util import color, gris


def dibujar_resultado(imagen: np.ndarray, cajas, hay: bool, resumen: str) -> np.ndarray:
    """Vista de comprobación: cajas verdes sobre el texto y un rótulo con el veredicto."""
    vista = color(imagen).copy()
    grosor = max(2, round(max(vista.shape[:2]) / 700))
    for caja in cajas:
        pts = np.asarray(caja, dtype=np.int32)
        if pts.shape == (4,):
            x1, y1, x2, y2 = pts
            pts = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], np.int32)
        cv2.polylines(vista, [pts.reshape(-1, 1, 2)], True, (40, 170, 40), grosor)
    escala = max(0.8, max(vista.shape[:2]) / 1400)
    texto = ("CON TEXTO" if hay else "SIN TEXTO") + f" - {resumen}"
    (tw, th), _ = cv2.getTextSize(texto, cv2.FONT_HERSHEY_SIMPLEX, escala, grosor)
    cv2.rectangle(vista, (0, 0), (tw + 24, th + 28), (40, 130, 40) if hay else (40, 40, 190), -1)
    cv2.putText(vista, texto, (12, th + 12), cv2.FONT_HERSHEY_SIMPLEX, escala, (255, 255, 255), grosor, cv2.LINE_AA)
    return vista


@nodo("texto/hay_texto", titulo="¿Hay texto? (clásico)", entradas={"imagen": IMAGEN},
      salidas={"datos": DATOS, "vista": IMAGEN, "mascara": IMAGEN},
      params=[Entero("contraste", 20, 3, 120,
                     ayuda="Cuánto más oscuro que su entorno debe ser un trazo para contar. Súbelo si "
                           "toma por texto el reverso transparentado"),
              Entero("alto_min", 6, 2, 100, etiqueta="alto mínimo de letra",
                     ayuda="En píxeles sobre la página reducida a 1500 px de lado"),
              Entero("alto_max", 70, 8, 400, etiqueta="alto máximo de letra"),
              Entero("letras_por_linea", 4, 2, 50, etiqueta="letras por línea",
                     ayuda="Mínimo de letras seguidas para aceptar un renglón"),
              Entero("lineas_min", 2, 1, 200, etiqueta="líneas mínimas",
                     ayuda="Renglones necesarios para decir que la página tiene texto"),
              Decimal("union", 2.5, 0.5, 8.0, paso=0.1, etiqueta="unión horizontal",
                      ayuda="Separación máxima entre letras de un mismo renglón, en altos de letra")],
      descripcion="Busca formas del tamaño de una letra alineadas en renglones. No usa redes neuronales: "
                  "es rápido y predecible. «datos» dice si hay texto y cuánto; «mascara» marca los renglones.")
def hay_texto(ctx, imagen, contraste, alto_min, alto_max, letras_por_linea, lineas_min, union):
    g = gris(imagen)
    f = min(1.0, 1500.0 / max(g.shape))
    p = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else g
    alto, ancho = p.shape
    bloque = 2 * max(7, alto_max // 2) + 1
    tinta = cv2.adaptiveThreshold(p, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, bloque, contraste)
    n, etiquetas, stats, centros = cv2.connectedComponentsWithStats(tinta, connectivity=8)
    h, w, area = stats[:, cv2.CC_STAT_HEIGHT], stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_AREA]
    es_letra = (h >= alto_min) & (h <= alto_max) & (w <= 5 * h) & (w >= 2) & (area >= 8)
    es_letra[0] = False
    vacio = {"hay_texto": False, "lineas": 0, "letras": 0, "cobertura": 0.0, "alto_letra": 0}
    mascara = np.zeros(g.shape, np.uint8)
    if not es_letra.any():
        return vacio, dibujar_resultado(imagen, [], False, "0 lineas"), mascara

    alto_letra = float(np.median(h[es_letra]))
    letras = es_letra[etiquetas].astype(np.uint8) * 255
    k = max(3, int(round(union * alto_letra)))
    franjas = cv2.morphologyEx(letras, cv2.MORPH_CLOSE, np.ones((1, k), np.uint8))
    nl, et_l, st_l, _ = cv2.connectedComponentsWithStats(franjas, connectivity=8)
    # Cuántas letras caen en cada franja
    cx = np.clip(np.rint(centros[es_letra, 0]).astype(int), 0, ancho - 1)
    cy = np.clip(np.rint(centros[es_letra, 1]).astype(int), 0, alto - 1)
    por_franja = np.bincount(et_l[cy, cx], minlength=nl)
    por_franja[0] = 0
    valida = (por_franja >= letras_por_linea) & (st_l[:, cv2.CC_STAT_WIDTH] >= 3 * alto_letra) \
        & (st_l[:, cv2.CC_STAT_HEIGHT] <= 4 * alto_letra)
    valida[0] = False

    cajas, cubierto = [], 0
    for i in np.flatnonzero(valida):
        x, y, bw, bh = st_l[i, :4]
        cubierto += int(bw) * int(bh)
        cajas.append([int(x / f), int(y / f), int((x + bw) / f), int((y + bh) / f)])
    lineas, total = int(valida.sum()), int(por_franja[valida].sum())
    hay = lineas >= lineas_min
    datos = {"hay_texto": bool(hay), "lineas": lineas, "letras": total,
             "cobertura": round(cubierto / float(alto * ancho), 4), "alto_letra": round(alto_letra / f, 1)}
    margen = max(2, int(round(alto_letra / f * 0.4)))
    for x1, y1, x2, y2 in cajas:
        cv2.rectangle(mascara, (x1 - margen, y1 - margen), (x2 + margen, y2 + margen), 255, -1)
    return datos, dibujar_resultado(imagen, cajas, hay, f"{lineas} lineas, {total} letras"), mascara
