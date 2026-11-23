"""Geometría: enderezar, girar, recortar y redimensionar."""
from __future__ import annotations

import cv2
import numpy as np

from ..registro import DATOS, IMAGEN, Decimal, Entero, Logico, Opcion, nodo
from ..util import gris, nivel_papel, tinta

_INTERPOLACION = {"área": cv2.INTER_AREA, "lineal": cv2.INTER_LINEAR, "cúbica": cv2.INTER_CUBIC,
                  "lanczos": cv2.INTER_LANCZOS4, "vecino": cv2.INTER_NEAREST}


def _relleno(imagen: np.ndarray, relleno: str):
    """Devuelve (modo de borde, valor) para ``warpAffine``."""
    if relleno == "replicar":
        return cv2.BORDER_REPLICATE, 0
    v = 255 if relleno == "blanco" else int(round(nivel_papel(imagen)))
    return cv2.BORDER_CONSTANT, (v, v, v) if imagen.ndim == 3 else v


def _girar(imagen: np.ndarray, angulo: float, relleno: str, expandir: bool = False,
           interpolacion=cv2.INTER_CUBIC) -> np.ndarray:
    alto, ancho = imagen.shape[:2]
    m = cv2.getRotationMatrix2D((ancho / 2, alto / 2), angulo, 1.0)
    if expandir:
        c, s = abs(m[0, 0]), abs(m[0, 1])
        nuevo_ancho, nuevo_alto = int(alto * s + ancho * c), int(alto * c + ancho * s)
        m[0, 2] += nuevo_ancho / 2 - ancho / 2
        m[1, 2] += nuevo_alto / 2 - alto / 2
        ancho, alto = nuevo_ancho, nuevo_alto
    modo, valor = _relleno(imagen, relleno)
    return cv2.warpAffine(imagen, m, (ancho, alto), flags=interpolacion, borderMode=modo, borderValue=valor)


def estimar_inclinacion(imagen: np.ndarray, angulo_max: float = 5.0, precision: float = 0.1) -> float:
    """Ángulo (grados) que hay que girar la página para que los renglones queden horizontales.

    Método del perfil de proyección: con el giro correcto, la suma de tinta por
    filas alterna renglones llenos e interlíneas vacías, y su variación es máxima.
    """
    g = gris(imagen)
    f = 1000.0 / max(g.shape)
    if f < 1:
        g = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    m = tinta(g)
    alto, ancho = m.shape
    # Se ignora un marco exterior: los bordes del escaneo falsean el perfil
    mb, ma = max(1, int(ancho * 0.04)), max(1, int(alto * 0.04))   # nunca 0: m[-0:] sería toda la imagen
    m[:ma], m[-ma:], m[:, :mb], m[:, -mb:] = 0, 0, 0, 0
    if not m.any():      # página en blanco: no hay renglones que medir
        return 0.0
    centro = (ancho / 2, alto / 2)

    def nota(a: float) -> float:
        r = cv2.warpAffine(m, cv2.getRotationMatrix2D(centro, a, 1.0), (ancho, alto), flags=cv2.INTER_NEAREST)
        perfil = r.sum(axis=1, dtype=np.float64)
        d = np.diff(perfil)
        return float((d * d).sum())

    def mejor(angulos) -> float:
        angulos = [float(a) for a in angulos if abs(a) <= angulo_max + 1e-6]
        notas = [nota(a) for a in angulos]
        if max(notas) <= 0:
            return 0.0
        # a igualdad de nota, el giro más pequeño
        return max(zip(notas, angulos), key=lambda na: (na[0], -abs(na[1])))[1]

    paso = max(0.5, precision)
    a = mejor(np.arange(-angulo_max, angulo_max + 1e-6, paso))
    while paso > precision:
        nuevo = max(precision, paso / 5)
        a = mejor(np.arange(a - paso, a + paso + 1e-6, nuevo))
        paso = nuevo
    return float(a)


@nodo("geometria/enderezar", titulo="Enderezar", entradas={"imagen": IMAGEN},
      salidas={"imagen": IMAGEN, "datos": DATOS},
      params=[Decimal("angulo_max", 5.0, 0.5, 45.0, paso=0.5, etiqueta="ángulo máximo",
                      ayuda="Inclinación máxima que se busca, en grados"),
              Decimal("precision", 0.1, 0.02, 1.0, paso=0.01, etiqueta="precisión"),
              Decimal("minimo", 0.05, 0.0, 2.0, paso=0.01, etiqueta="no girar si menos de",
                      ayuda="Por debajo de este ángulo la página se deja como está, sin remuestrear"),
              Opcion("relleno", "papel", ["papel", "blanco", "replicar"])],
      descripcion="Mide la inclinación de los renglones y gira la página para dejarlos horizontales.")
def enderezar(ctx, imagen, angulo_max, precision, minimo, relleno):
    a = estimar_inclinacion(imagen, angulo_max, precision)
    if abs(a) < minimo:
        return imagen, {"angulo": round(a, 3), "girada": False}
    return _girar(imagen, a, relleno), {"angulo": round(a, 3), "girada": True}


@nodo("geometria/rotar", titulo="Rotar", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Decimal("angulo", 0.0, -180.0, 180.0, paso=0.1, etiqueta="ángulo", ayuda="Positivo = antihorario"),
              Logico("expandir", False, ayuda="Agranda el lienzo para no cortar las esquinas"),
              Opcion("relleno", "papel", ["papel", "blanco", "replicar"])],
      descripcion="Gira la página un ángulo fijo.")
def rotar(ctx, imagen, angulo, expandir, relleno):
    return _girar(imagen, angulo, relleno, expandir)


@nodo("geometria/girar90", titulo="Girar 90° / voltear", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Opcion("giro", "90° derecha", ["90° derecha", "90° izquierda", "180°", "espejo horizontal",
                                              "espejo vertical"])],
      descripcion="Giros exactos, sin pérdida. El espejo horizontal sirve para leer el reverso transparentado.")
def girar90(ctx, imagen, giro):
    if giro == "90° derecha":
        return cv2.rotate(imagen, cv2.ROTATE_90_CLOCKWISE)
    if giro == "90° izquierda":
        return cv2.rotate(imagen, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if giro == "180°":
        return cv2.rotate(imagen, cv2.ROTATE_180)
    return cv2.flip(imagen, 1 if giro == "espejo horizontal" else 0)


@nodo("geometria/bordes", titulo="Quitar bordes de escaneo", entradas={"imagen": IMAGEN},
      salidas={"imagen": IMAGEN, "borde": IMAGEN},
      params=[Entero("umbral", 90, 0, 255, ayuda="Más oscuro que esto y pegado al borde = borde de escaneo"),
              Entero("margen", 6, 0, 60, ayuda="Píxeles extra que se limpian alrededor, por la penumbra"),
              Decimal("area_min", 0.05, 0.0, 5.0, paso=0.01, etiqueta="área mínima (%)",
                      ayuda="Las zonas oscuras más pequeñas que este porcentaje de la página no se tocan"),
              Opcion("relleno", "papel", ["papel", "blanco"])],
      descripcion="Borra las bandas negras del lomo y del borde del escáner: zonas oscuras que tocan "
                  "el borde de la imagen.")
def bordes(ctx, imagen, umbral, margen, area_min, relleno):
    g = gris(imagen)
    # Un borde de escaneo es bastante más oscuro que el papel: con papel oscuro (o una página
    # en negativo) un umbral fijo tomaría la página entera por borde
    umbral = min(umbral, int(0.55 * nivel_papel(g)))
    oscuro = cv2.morphologyEx((g < umbral).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, etiquetas, stats, _ = cv2.connectedComponentsWithStats(oscuro, connectivity=8)
    alto, ancho = g.shape
    toca = np.zeros(n, dtype=bool)
    for franja in (etiquetas[0], etiquetas[-1], etiquetas[:, 0], etiquetas[:, -1]):
        toca[np.unique(franja)] = True
    toca[0] = False
    toca &= stats[:, cv2.CC_STAT_AREA] >= area_min / 100.0 * alto * ancho
    mascara = toca[etiquetas].astype(np.uint8) * 255
    cubierto = np.count_nonzero(mascara) / mascara.size
    if cubierto > 0.40:
        ctx.avisar(f"El «borde» ocuparía el {cubierto:.0%} de la página: no se ha borrado nada. "
                   "Puede ser una página muy oscura o en negativo")
        return imagen, np.zeros_like(mascara)
    if margen > 0:
        mascara = cv2.dilate(mascara, cv2.getStructuringElement(cv2.MORPH_RECT, (2 * margen + 1, 2 * margen + 1)))
    salida = imagen.copy()
    salida[mascara > 0] = 255 if relleno == "blanco" else int(round(nivel_papel(imagen)))
    return salida, mascara


def _rectas(tinta_r: np.ndarray, horizontal: bool, tramo: int):
    """Trazos rectos en una dirección: la máscara de tramos y sus componentes ya unidos
    (una línea cortada o algo torcida queda como una sola pieza)."""
    forma = (tramo, 1) if horizontal else (1, tramo)
    tramos = cv2.morphologyEx(tinta_r, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, forma))
    puente = (tramo, 7) if horizontal else (7, tramo)
    unidas = cv2.morphologyEx(tramos, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, puente))
    n, et, st, _ = cv2.connectedComponentsWithStats(unidas, connectivity=8)
    return tramos, n, et, st


def _se_tocan(a, b, holgura: int) -> bool:
    return not (a["x"] - holgura > b["x"] + b["w"] or b["x"] - holgura > a["x"] + a["w"]
                or a["y"] - holgura > b["y"] + b["h"] or b["y"] - holgura > a["y"] + a["h"])


@nodo("geometria/marco", titulo="Quitar marco de la hoja", entradas={"imagen": IMAGEN},
      salidas={"imagen": IMAGEN, "marco": IMAGEN, "datos": DATOS},
      params=[Decimal("zona", 15.0, 1.0, 45.0, paso=0.5, etiqueta="zona exterior (%)",
                      ayuda="Una línea cuenta como marco si está a esta distancia del borde de la imagen, como mucho"),
              Decimal("largo_min", 15.0, 2.0, 100.0, paso=1.0, etiqueta="largo mínimo (%)",
                      ayuda="Largo de la línea respecto al lado de la página. Las rayas cortas no se tocan"),
              Decimal("grosor_max", 1.5, 0.1, 10.0, paso=0.1, etiqueta="grosor máximo (%)",
                      ayuda="Las bandas más gruesas no son una línea de marco (para esas, «Quitar bordes de escaneo»)"),
              Decimal("tinta_fuera", 0.02, 0.0, 5.0, paso=0.01, etiqueta="tinta permitida fuera (%)",
                      ayuda="Si entre la línea y el borde hay más tinta que esto (un número de página, una "
                            "cabecera), la línea es del contenido y se conserva"),
              Logico("recuadros", True, etiqueta="recuadros enteros",
                     ayuda="Borra también los otros lados de un recuadro cuando uno de ellos es marco"),
              Logico("limpiar_fuera", True, etiqueta="limpiar lo de fuera",
                     ayuda="Borra también las motas y restos que queden entre la línea y el borde"),
              Entero("margen", 4, 0, 40, ayuda="Píxeles extra que se limpian alrededor de la línea"),
              Opcion("relleno", "papel", ["papel", "blanco"])],
      descripcion="Borra las líneas rectas largas que rodean la página (el canto de la hoja, la sombra del "
                  "fotocopiado, el recuadro de un recorte pegado): no son texto y estorban al OCR. Solo actúa "
                  "cerca del borde y cuando fuera de la línea no hay nada. Los filetes de las tablas se "
                  "respetan: se reconocen porque otras rayas los cruzan por el medio. «marco» es la máscara "
                  "de lo borrado.")
def marco(ctx, imagen, zona, largo_min, grosor_max, tinta_fuera, recuadros, limpiar_fuera, margen, relleno):
    g = gris(imagen)
    alto0, ancho0 = g.shape
    f = min(1.0, 1500.0 / max(g.shape))
    t = tinta(g)
    if f < 1:   # se reduce la máscara, no la imagen: así no se pierden las líneas de un píxel
        t = (cv2.resize(t, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) > 50).astype(np.uint8) * 255
    alto, ancho = t.shape
    tramo = max(25, int(0.03 * max(alto, ancho)))
    datos = {"lineas": 0, "cajas": []}
    nada = np.zeros(g.shape, np.uint8)

    # 1. Líneas rectas, largas y finas en las dos direcciones
    tramos = {}
    lineas = []
    todas = np.zeros_like(t)
    for horizontal in (True, False):
        tramos[horizontal], n, et, st = _rectas(t, horizontal, tramo)
        lado, otro = (ancho, alto) if horizontal else (alto, ancho)
        for i in range(1, n):
            x, y, w, h = (int(v) for v in st[i, :4])
            largo, grosor = (w, h) if horizontal else (h, w)
            # se admite la inclinación de una página sin enderezar del todo (unos 2 grados)
            if largo < largo_min / 100.0 * lado or grosor > grosor_max / 100.0 * otro + 0.035 * largo:
                continue
            pixeles = et[y:y + h, x:x + w] == i
            todas[y:y + h, x:x + w][pixeles] = 255
            lineas.append({"horizontal": horizontal, "x": x, "y": y, "w": w, "h": h, "pixeles": pixeles})
    if not lineas:
        return imagen, nada, datos

    # 2. Las de una tabla no son marco: otras rayas las tocan por el medio, no solo en las esquinas
    for ln in lineas:
        x, y, w, h = ln["x"], ln["y"], ln["w"], ln["h"]
        largo = w if ln["horizontal"] else h
        punta = max(12, int(0.10 * largo))   # las esquinas de un recuadro no cuentan
        cerca = tramo        # los filetes interiores de una tabla a veces no llegan a tocar el exterior
        if ln["horizontal"]:
            zona_media = (slice(max(0, y - cerca), y + h + cerca), slice(x + punta, x + w - punta))
        else:
            zona_media = (slice(y + punta, y + h - punta), slice(max(0, x - cerca), x + w + cerca))
        cruces = tramos[not ln["horizontal"]][zona_media]
        ln["tabla"] = int(np.count_nonzero(cruces)) >= tramo // 2
    # ...y los lados que cierran esa tabla tampoco, aunque nada los cruce (tablas solo con columnas)
    cambio = True
    while cambio:
        cambio = False
        for ln in lineas:
            if not ln["tabla"] and any(o["tabla"] and o["horizontal"] != ln["horizontal"] and _se_tocan(ln, o, 8)
                                       for o in lineas):
                ln["tabla"] = cambio = True

    # 3. Tinta que no es línea y tiene tamaño de letra o mayor (motas y rayitas no cuentan)
    resto = cv2.bitwise_and(t, cv2.bitwise_not(cv2.dilate(todas, np.ones((5, 5), np.uint8))))
    n, et_r, st_r, _ = cv2.connectedComponentsWithStats(resto, connectivity=8)
    real = (st_r[:, cv2.CC_STAT_AREA] >= 8) & (st_r[:, cv2.CC_STAT_HEIGHT] >= 5) & (st_r[:, cv2.CC_STAT_WIDTH] >= 3)
    real[0] = False
    resto = real[et_r].astype(np.uint8)
    # Lo que tiene tamaño de letra o de cifra no se borra nunca con «limpiar lo de fuera», aunque sea
    # tan poca tinta que no impida reconocer el marco: puede ser un número de página suelto
    tope_letra = max(40, int(0.06 * max(alto, ancho)))
    letra = real & (st_r[:, cv2.CC_STAT_HEIGHT] <= tope_letra) & (st_r[:, cv2.CC_STAT_WIDTH] <= tope_letra)
    letras = letra[et_r].astype(np.uint8) * 255

    def vacia(zona_px) -> bool:
        franja = resto[zona_px]
        return franja.size == 0 or franja.sum() <= tinta_fuera / 100.0 * franja.size

    # 4. Marco seguro: cerca del borde y sin nada entre la línea y el borde
    for ln in lineas:
        x, y, w, h = ln["x"], ln["y"], ln["w"], ln["h"]
        ln["marco"], ln["fuera"] = False, None
        if ln["tabla"]:
            continue
        if ln["horizontal"]:
            centro, lado = y + h / 2.0, alto
            inicio = centro < lado / 2.0
            filas = slice(0, y + h) if inicio else slice(y, alto)
            entera, tramo_propio = (filas, slice(0, ancho)), (filas, slice(x, x + w))
        else:
            centro, lado = x + w / 2.0, ancho
            inicio = centro < lado / 2.0
            columnas = slice(0, x + w) if inicio else slice(x, ancho)
            entera, tramo_propio = (slice(0, alto), columnas), (slice(y, y + h), columnas)
        if (centro if inicio else lado - centro) > zona / 100.0 * lado:
            continue
        # si todo el lado está vacío se limpia entero (se lleva los trozos sueltos de la misma línea)
        if vacia(entera):
            ln["marco"], ln["fuera"] = True, entera
        elif vacia(tramo_propio):
            ln["marco"], ln["fuera"] = True, tramo_propio

    # 5. Los demás lados de un recuadro del que ya se ha reconocido un lado
    if recuadros:
        cambio = True
        while cambio:
            cambio = False
            for ln in lineas:
                if ln["marco"] or ln["tabla"]:
                    continue
                if any(o["marco"] and o["horizontal"] != ln["horizontal"] and _se_tocan(ln, o, 8) for o in lineas):
                    ln["marco"] = cambio = True

    mascara = np.zeros((alto, ancho), np.uint8)
    conservar = np.zeros((alto, ancho), np.uint8)
    for ln in lineas:
        if not ln["marco"]:
            continue
        x, y, w, h = ln["x"], ln["y"], ln["w"], ln["h"]
        mascara[y:y + h, x:x + w][ln["pixeles"]] = 255
        if limpiar_fuera and ln["fuera"] is not None:
            mascara[ln["fuera"]] = 255
            conservar[ln["fuera"]] = letras[ln["fuera"]]
        datos["cajas"].append([int(x / f), int(y / f), int((x + w) / f), int((y + h) / f)])
    datos["lineas"] = len(datos["cajas"])
    if not datos["lineas"]:
        return imagen, nada, datos

    conservar = cv2.dilate(conservar, np.ones((7, 7), np.uint8))
    if f < 1:
        mascara = cv2.resize(mascara, (ancho0, alto0), interpolation=cv2.INTER_NEAREST)
        conservar = cv2.resize(conservar, (ancho0, alto0), interpolation=cv2.INTER_NEAREST)
    extra = margen + int(round(2 / f))      # la reducción desplaza la línea un par de píxeles
    mascara = cv2.dilate(mascara, cv2.getStructuringElement(cv2.MORPH_RECT, (2 * extra + 1, 2 * extra + 1)))
    mascara[conservar > 0] = 0
    salida = imagen.copy()
    salida[mascara > 0] = 255 if relleno == "blanco" else int(round(nivel_papel(imagen)))
    return salida, mascara, datos


@nodo("geometria/recortar_contenido", titulo="Recortar al contenido", entradas={"imagen": IMAGEN},
      salidas={"imagen": IMAGEN, "datos": DATOS},
      params=[Entero("margen", 40, 0, 1000, ayuda="Blanco que se deja alrededor del contenido"),
              Entero("area_min", 40, 1, 5000, etiqueta="área mínima",
                     ayuda="Las manchas más pequeñas no cuentan como contenido"),
              Entero("umbral", 0, 0, 255, ayuda="0 = automático")],
      descripcion="Recorta la página a la caja que contiene toda la tinta. Úsalo después de quitar los "
                  "bordes de escaneo. «datos» lleva la caja para poder deshacer el recorte.")
def recortar_contenido(ctx, imagen, margen, area_min, umbral):
    m = tinta(imagen, umbral)
    n, _, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    validas = stats[1:][stats[1:, cv2.CC_STAT_AREA] >= area_min]
    alto, ancho = m.shape
    if len(validas) == 0:
        return imagen, {"caja": [0, 0, ancho, alto], "recortada": False}
    x1 = max(0, int(validas[:, cv2.CC_STAT_LEFT].min()) - margen)
    y1 = max(0, int(validas[:, cv2.CC_STAT_TOP].min()) - margen)
    x2 = min(ancho, int((validas[:, cv2.CC_STAT_LEFT] + validas[:, cv2.CC_STAT_WIDTH]).max()) + margen)
    y2 = min(alto, int((validas[:, cv2.CC_STAT_TOP] + validas[:, cv2.CC_STAT_HEIGHT]).max()) + margen)
    return imagen[y1:y2, x1:x2], {"caja": [x1, y1, x2, y2], "recortada": True}


@nodo("geometria/margenes", titulo="Recortar márgenes (%)", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Decimal("izquierda", 0.0, 0.0, 45.0, paso=0.5), Decimal("arriba", 0.0, 0.0, 45.0, paso=0.5),
              Decimal("derecha", 0.0, 0.0, 45.0, paso=0.5), Decimal("abajo", 0.0, 0.0, 45.0, paso=0.5)],
      descripcion="Quita un porcentaje fijo de cada lado.")
def margenes(ctx, imagen, izquierda, arriba, derecha, abajo):
    alto, ancho = imagen.shape[:2]
    x1, x2 = int(ancho * izquierda / 100), ancho - int(ancho * derecha / 100)
    y1, y2 = int(alto * arriba / 100), alto - int(alto * abajo / 100)
    return imagen[y1:max(y2, y1 + 1), x1:max(x2, x1 + 1)]


@nodo("geometria/redimensionar", titulo="Redimensionar", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Decimal("factor", 1.0, 0.1, 4.0, paso=0.05),
              Opcion("interpolacion", "automática", ["automática", *_INTERPOLACION], etiqueta="interpolación")],
      descripcion="Amplía o reduce. Ampliar letra pequeña (x1,5 o x2) antes de binarizar suele mejorar el OCR.")
def redimensionar(ctx, imagen, factor, interpolacion):
    if abs(factor - 1.0) < 1e-6:
        return imagen
    inter = (cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC) if interpolacion == "automática" \
        else _INTERPOLACION[interpolacion]
    alto, ancho = imagen.shape[:2]
    tam = (max(1, int(round(ancho * factor))), max(1, int(round(alto * factor))))
    return cv2.resize(imagen, tam, interpolation=inter)
