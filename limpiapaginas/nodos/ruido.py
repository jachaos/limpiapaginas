"""Ruido, suavizado y morfología."""
from __future__ import annotations

import cv2
import numpy as np

from .. import dispositivo
from ..registro import IMAGEN, Decimal, Entero, Opcion, nodo
from ..util import gris, nivel_papel, tinta
from .fondo import _gauss_gpu


@nodo("ruido/gaussiano", titulo="Desenfoque gaussiano", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      gpu=True,
      params=[Entero("tam", 5, 1, 99, impar=True, etiqueta="tamaño"),
              Decimal("sigma", 0.0, 0.0, 50.0, paso=0.1, ayuda="0 = se calcula a partir del tamaño")],
      descripcion="Suaviza el grano fino. Antes de binarizar, un tamaño de 3 o 5 evita letras con bordes rotos.")
def gaussiano(ctx, imagen, tam, sigma):
    def cpu():
        return dispositivo.mat(cv2.GaussianBlur(dispositivo.umat(imagen), (tam, tam), sigma))

    def gpu():
        return dispositivo.a_imagen(_gauss_gpu(dispositivo.a_tensor(imagen), tam, sigma))

    return dispositivo.con_gpu(ctx, gpu, cpu)


@nodo("ruido/mediana", titulo="Filtro de mediana", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Entero("tam", 3, 3, 31, impar=True, etiqueta="tamaño")],
      descripcion="Quita puntos sueltos (sal y pimienta) sin emborronar los bordes. Con tamaños grandes "
                  "se come los puntos de las íes y las tildes.")
def mediana(ctx, imagen, tam):
    return dispositivo.mat(cv2.medianBlur(dispositivo.umat(imagen), tam))


@nodo("ruido/bilateral", titulo="Filtro bilateral", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Entero("diametro", 7, 1, 25, etiqueta="diámetro"),
              Entero("sigma_color", 40, 1, 200, etiqueta="sigma color",
                     ayuda="Cuánta diferencia de gris se considera «el mismo tono»"),
              Entero("sigma_espacio", 40, 1, 200, etiqueta="sigma espacio")],
      descripcion="Alisa el papel y respeta los bordes de las letras. Más lento que el gaussiano.")
def bilateral(ctx, imagen, diametro, sigma_color, sigma_espacio):
    return dispositivo.mat(cv2.bilateralFilter(dispositivo.umat(imagen), diametro, sigma_color, sigma_espacio))


@nodo("ruido/nlm", titulo="Quitar grano (medias no locales)", entradas={"imagen": IMAGEN},
      salidas={"imagen": IMAGEN},
      params=[Decimal("fuerza", 10.0, 1.0, 40.0, paso=0.5, ayuda="Más alto quita más grano y también más detalle"),
              Entero("plantilla", 7, 3, 11, impar=True), Entero("busqueda", 21, 7, 35, impar=True, etiqueta="búsqueda")],
      descripcion="El mejor filtro clásico contra el grano del papel y del escáner. Es lento en páginas grandes.")
def nlm(ctx, imagen, fuerza, plantilla, busqueda):
    src = dispositivo.umat(imagen)
    if imagen.ndim == 2:
        return dispositivo.mat(cv2.fastNlMeansDenoising(src, None, fuerza, plantilla, busqueda))
    return dispositivo.mat(cv2.fastNlMeansDenoisingColored(src, None, fuerza, fuerza, plantilla, busqueda))


@nodo("ruido/motas", titulo="Quitar motas", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN, "quitado": IMAGEN},
      params=[Entero("area_max", 12, 1, 5000, etiqueta="área máxima",
                     ayuda="Se borran las manchas de tinta de hasta estos píxeles"),
              Entero("proteccion", 20, 0, 200, etiqueta="protección",
                     ayuda="Las manchas pequeñas a menos de esta distancia de una letra se conservan: son "
                           "puntos, tildes, comas o diéresis. 0 = no proteger nada"),
              Entero("umbral", 0, 0, 255, ayuda="Gris por debajo del cual algo es tinta. 0 = automático"),
              Opcion("relleno", "blanco", ["blanco", "papel"])],
      descripcion="Borra manchas pequeñas y aisladas (polvo, moho, restos de binarización). No toca las que "
                  "están junto al texto, para no perder tildes ni puntuación. «quitado» muestra lo borrado.")
def motas(ctx, imagen, area_max, proteccion, umbral, relleno):
    m = tinta(imagen, umbral)
    n, etiquetas, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    areas = stats[:, cv2.CC_STAT_AREA]
    pequena = areas <= area_max
    pequena[0] = False
    if proteccion > 0:
        grande = ~pequena
        grande[0] = False
        cerca = cv2.dilate(grande[etiquetas].astype(np.uint8),
                           cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * proteccion + 1, 2 * proteccion + 1)))
        protegidas = np.unique(etiquetas[cerca > 0])
        pequena[protegidas] = False
    quitar = pequena[etiquetas]
    total = int(np.count_nonzero(m))
    if total and np.count_nonzero(quitar) > 0.5 * total:
        # letra muy fina o de baja resolución: todo «parece» una mota y se borraría el texto
        ctx.avisar("Más de la mitad de la tinta son manchas de hasta «área máxima»: parece letra muy "
                   "pequeña, no motas. No se ha borrado nada; baja «área máxima»")
        return imagen, np.zeros(m.shape, np.uint8)
    quitar_d = cv2.dilate(quitar.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    valor = 255 if relleno == "blanco" else int(round(nivel_papel(imagen)))
    salida = imagen.copy()
    salida[quitar_d] = valor
    return salida, quitar.astype(np.uint8) * 255


_OPERACIONES = {"abrir": cv2.MORPH_OPEN, "cerrar": cv2.MORPH_CLOSE, "erosionar": cv2.MORPH_ERODE,
                "dilatar": cv2.MORPH_DILATE, "gradiente": cv2.MORPH_GRADIENT,
                "sombrero blanco": cv2.MORPH_TOPHAT, "sombrero negro": cv2.MORPH_BLACKHAT}
_FORMAS = {"rectángulo": cv2.MORPH_RECT, "elipse": cv2.MORPH_ELLIPSE, "cruz": cv2.MORPH_CROSS}


@nodo("ruido/morfologia", titulo="Morfología", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Opcion("operacion", "cerrar", list(_OPERACIONES), etiqueta="operación"),
              Opcion("forma", "elipse", list(_FORMAS)),
              Entero("ancho", 3, 1, 201), Entero("alto", 3, 1, 201), Entero("repeticiones", 1, 1, 20),
              Opcion("sobre", "tinta", ["tinta", "claro"],
                     ayuda="«tinta»: la operación actúa sobre lo oscuro (dilatar engorda las letras). "
                           "«claro»: comportamiento directo de OpenCV, sobre lo blanco")],
      descripcion="Engorda, adelgaza, une o separa trazos. «cerrar» repara letras rotas; «abrir» quita "
                  "puntos y trazos finos. Con ancho y alto distintos actúa solo en una dirección.")
def morfologia(ctx, imagen, operacion, forma, ancho, alto, repeticiones, sobre):
    nucleo = cv2.getStructuringElement(_FORMAS[forma], (ancho, alto))
    src = cv2.bitwise_not(imagen) if sobre == "tinta" else imagen
    res = cv2.morphologyEx(dispositivo.umat(src), _OPERACIONES[operacion], nucleo, iterations=repeticiones)
    res = dispositivo.mat(res)
    return cv2.bitwise_not(res) if sobre == "tinta" else res
