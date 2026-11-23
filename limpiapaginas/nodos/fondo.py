"""Fondo y papel: iluminación desigual, papel amarillento y texto transparentado."""
from __future__ import annotations

import cv2
import numpy as np

from .. import dispositivo
from ..registro import IMAGEN, Decimal, Entero, Opcion, nodo
from ..util import gris, impar, nivel_papel, por_canal


def _fondo_cpu(canal: np.ndarray, tam: int, suavizado: int) -> np.ndarray:
    """Estima el papel sin la tinta: cierre morfológico + suavizado, a resolución reducida."""
    escala = max(1, tam // 15)
    alto, ancho = canal.shape
    peq = cv2.resize(canal, (max(1, ancho // escala), max(1, alto // escala)), interpolation=cv2.INTER_AREA)
    k = impar(max(3, round(tam / escala)))
    peq = cv2.morphologyEx(peq, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    s = impar(max(1, round(suavizado / escala)))
    if s > 1:
        peq = cv2.GaussianBlur(peq, (s, s), 0)
    return cv2.resize(peq, (ancho, alto), interpolation=cv2.INTER_LINEAR)


def _fondo_gpu(x, tam: int, suavizado: int):
    """Lo mismo que ``_fondo_cpu`` con tensores 1xCxHxW."""
    import torch.nn.functional as F
    escala = max(1, tam // 15)
    alto, ancho = x.shape[-2:]
    peq = F.interpolate(x, size=(max(1, alto // escala), max(1, ancho // escala)), mode="area")
    k = impar(max(3, round(tam / escala)))
    r = k // 2
    peq = F.max_pool2d(F.pad(peq, (r, r, r, r), mode="replicate"), k, stride=1)          # dilatación
    peq = -F.max_pool2d(F.pad(-peq, (r, r, r, r), mode="replicate"), k, stride=1)        # erosión
    s = impar(max(1, round(suavizado / escala)))
    if s > 1:
        peq = _gauss_gpu(peq, s, 0.0)
    return F.interpolate(peq, size=(alto, ancho), mode="bilinear", align_corners=False)


def _gauss_gpu(x, tam: int, sigma: float):
    """Desenfoque gaussiano separable en GPU, con los mismos pesos que OpenCV."""
    import torch
    import torch.nn.functional as F
    r = tam // 2
    k = torch.from_numpy(cv2.getGaussianKernel(tam, sigma, cv2.CV_32F).ravel()).to(x.device)   # los de OpenCV
    c = x.shape[1]
    modo = "reflect" if r < min(x.shape[-2:]) else "replicate"
    x = F.conv2d(F.pad(x, (r, r, 0, 0), mode=modo), k.view(1, 1, 1, -1).repeat(c, 1, 1, 1), groups=c)
    return F.conv2d(F.pad(x, (0, 0, r, r), mode=modo), k.view(1, 1, -1, 1).repeat(c, 1, 1, 1), groups=c)


@nodo("fondo/aplanar", titulo="Aplanar fondo", entradas={"imagen": IMAGEN},
      salidas={"imagen": IMAGEN, "fondo": IMAGEN}, gpu=True,
      params=[Entero("tam", 61, 15, 501, impar=True, etiqueta="tamaño",
                     ayuda="Debe ser mayor que el grosor de las letras más grandes; si es demasiado pequeño, las vacía"),
              Entero("suavizado", 31, 1, 301, impar=True),
              Opcion("modo", "dividir", ["dividir", "restar"],
                     ayuda="«dividir» corrige sombras e iluminación; «restar» es más suave con tinta muy tenue")],
      descripcion="Calcula cómo sería el papel sin tinta y lo elimina: quita sombras del lomo, manchas "
                  "amplias, degradados y el tono amarillento. El papel queda blanco y la tinta conserva su contraste.")
def aplanar(ctx, imagen, tam, suavizado, modo):
    def cpu():
        def uno(c):
            f = _fondo_cpu(c, tam, suavizado)
            if modo == "dividir":
                return cv2.divide(c, f, scale=255), f
            return cv2.subtract(255, cv2.subtract(f, c)), f
        if imagen.ndim == 2:
            return uno(imagen)
        pares = [uno(c) for c in cv2.split(imagen)]
        return cv2.merge([p[0] for p in pares]), cv2.merge([p[1] for p in pares])

    def gpu():
        x = dispositivo.a_tensor(imagen)
        f = _fondo_gpu(x, tam, suavizado)
        if modo == "dividir":
            y = x / f.clamp(min=1.0) * 255.0
        else:
            y = 255.0 - (f - x).clamp(min=0)
        return dispositivo.a_imagen(y), dispositivo.a_imagen(f)

    return dispositivo.con_gpu(ctx, gpu, cpu)


@nodo("fondo/blanquear", titulo="Blanquear papel", entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN},
      params=[Decimal("percentil", 80.0, 40.0, 99.5, paso=0.5,
                      ayuda="Qué parte de la página se considera papel. Súbelo en páginas con mucha tinta o láminas"),
              Entero("margen", 12, 0, 100, ayuda="Cuánto por debajo del gris del papel pasa ya a blanco puro"),
              Entero("negro", 0, 0, 200, ayuda="Punto negro: súbelo para oscurecer tinta desvaída")],
      descripcion="Mide el gris del papel de esta página y lo lleva a blanco. Es un «niveles» que se ajusta solo.")
def blanquear(ctx, imagen, percentil, margen, negro):
    blanco = max(nivel_papel(imagen, percentil) - margen, negro + 1.0)
    x = np.arange(256, dtype=np.float32)
    lut = np.rint(np.clip((x - negro) / (blanco - negro), 0, 1) * 255).astype(np.uint8)
    return cv2.LUT(imagen, lut)


@nodo("fondo/transparencia", titulo="Quitar texto transparentado", entradas={"imagen": IMAGEN},
      salidas={"imagen": IMAGEN, "tinta": IMAGEN},
      params=[Entero("fuerte", 0, 0, 255, etiqueta="umbral tinta segura",
                     ayuda="Más oscuro que esto es tinta del anverso sin duda. 0 = automático"),
              Entero("debil", 0, 0, 255, etiqueta="umbral tinta dudosa",
                     ayuda="Hasta este gris se conserva solo si toca tinta segura. 0 = automático"),
              Opcion("relleno", "blanco", ["blanco", "papel", "atenuar"],
                     ayuda="Qué se pone donde no hay tinta del anverso"),
              Decimal("fuerza", 0.85, 0.0, 1.0, paso=0.05, ayuda="Solo con «atenuar»: 1 borra del todo")],
      descripcion="El texto del reverso que se transparenta es más claro que el del anverso. Se conserva la "
                  "tinta oscura y los grises que la tocan (bordes de las letras); los trazos claros sueltos se "
                  "borran. La salida «tinta» es la máscara de lo conservado.")
def transparencia(ctx, imagen, fuerte, debil, relleno, fuerza):
    g = gris(imagen)
    papel = nivel_papel(g)
    if fuerte <= 0:
        otsu, _ = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        oscuros = g[g <= otsu]
        # En una página ya en blanco y negro puro Otsu da 0 y «g < 0» no conservaría nada:
        # el umbral nunca baja del gris medio de la tinta.
        fuerte = max(float(otsu) * 0.85, float(oscuros.mean()) + 1.0 if oscuros.size else 1.0)
    if debil <= 0:
        debil = fuerte + 0.45 * (papel - fuerte)
    debil = max(debil, fuerte)
    dudosa = (g < debil).astype(np.uint8)
    n, etiquetas = cv2.connectedComponents(dudosa, connectivity=8)
    con_tinta = np.zeros(n, dtype=bool)
    con_tinta[np.unique(etiquetas[g < fuerte])] = True
    con_tinta[0] = False
    tinta = con_tinta[etiquetas].astype(np.uint8) * 255
    conservar = cv2.dilate(tinta, np.ones((3, 3), np.uint8)) > 0     # un píxel de borde suavizado
    if imagen.ndim == 3:
        conservar = conservar[:, :, None]
    if relleno == "atenuar":
        lejos = imagen.astype(np.float32)
        lejos = papel + (lejos - papel) * (1.0 - fuerza)
        salida = np.where(conservar, imagen, np.clip(lejos, 0, 255).astype(np.uint8))
    else:
        valor = 255 if relleno == "blanco" else int(round(papel))
        salida = np.where(conservar, imagen, np.uint8(valor))
    return salida.astype(np.uint8), tinta
