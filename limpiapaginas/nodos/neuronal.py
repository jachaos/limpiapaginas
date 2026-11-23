"""Nodos basados en redes neuronales (ONNX Runtime; usan la GPU si está disponible)."""
from __future__ import annotations

import functools
from pathlib import Path

import cv2
import numpy as np

from .. import dispositivo
from ..config import CONFIG
from ..registro import DATOS, IMAGEN, Decimal, Entero, Logico, Opcion, Texto, nodo
from ..util import color, gris
from .texto import dibujar_resultado


@functools.lru_cache(maxsize=8)
def _sesion(ruta: str, proveedores: tuple):
    import onnxruntime as ort
    if "CUDAExecutionProvider" in proveedores and hasattr(ort, "preload_dlls"):
        try:
            import torch  # noqa: F401  (sus librerías de CUDA/cuDNN sirven a ONNX Runtime)
            ort.preload_dlls()
        except Exception:
            pass
    opciones = ort.SessionOptions()
    opciones.log_severity_level = 3
    return ort.InferenceSession(ruta, sess_options=opciones, providers=list(proveedores))


def sesion_onnx(ctx, ruta: Path):
    """Abre (o reutiliza) un modelo ONNX en el dispositivo elegido y anota si corre en GPU."""
    if not ruta.exists():
        raise FileNotFoundError(f"No se encuentra el modelo: {ruta}")
    try:
        s = _sesion(str(ruta), tuple(dispositivo.proveedores_onnx()))
    except Exception as e:
        if dispositivo.proveedores_onnx()[0] == "CPUExecutionProvider":
            raise
        ctx.avisar(f"El modelo no arrancó en GPU ({type(e).__name__}); se usa la CPU")
        s = _sesion(str(ruta), ("CPUExecutionProvider",))
    ctx.uso_gpu = s.get_providers()[0] != "CPUExecutionProvider"
    return s


def _ruta_modelo(nombre: str) -> Path:
    p = Path(nombre).expanduser()
    return p if p.is_absolute() else CONFIG.carpeta_modelos / p


@nodo("neuronal/detector_texto", titulo="Detector de texto (red neuronal)", entradas={"imagen": IMAGEN},
      salidas={"datos": DATOS, "vista": IMAGEN, "mascara": IMAGEN}, gpu=True,
      params=[Texto("modelo", "PP-OCRv6_det_small.onnx", ayuda="Fichero .onnx en la carpeta «modelos»"),
              Entero("lado_max", 1280, 320, 4000, etiqueta="lado máximo",
                     ayuda="La página se reduce a este tamaño para la red. Más grande detecta letra más pequeña y tarda más"),
              Decimal("umbral", 0.3, 0.05, 0.95, paso=0.01, ayuda="Probabilidad mínima por píxel"),
              Decimal("umbral_caja", 0.6, 0.05, 0.98, paso=0.01, etiqueta="umbral de caja",
                      ayuda="Confianza media mínima para aceptar un renglón"),
              Decimal("expandir", 1.6, 1.0, 3.0, paso=0.05, ayuda="Cuánto se agranda cada caja detectada"),
              Decimal("alto_max", 12.0, 1.0, 100.0, paso=0.5, etiqueta="alto máximo (%)",
                      ayuda="Se descartan las cajas más altas que este porcentaje de la página: una forma "
                            "grande (un círculo, un marco) puede parecerle una letra gigante"),
              Entero("lineas_min", 1, 1, 200, etiqueta="líneas mínimas")],
      descripcion="Detector de renglones PP-OCR (DBNet). Más robusto que el clásico con fondos sucios, "
                  "texto inclinado y rótulos sueltos, y distingue mejor el texto de los grabados.")
def detector_texto(ctx, imagen, modelo, lado_max, umbral, umbral_caja, expandir, alto_max, lineas_min):
    s = sesion_onnx(ctx, _ruta_modelo(modelo))
    bgr = color(imagen)
    alto, ancho = bgr.shape[:2]
    f = min(1.0, lado_max / max(alto, ancho))
    na, nw = max(32, int(round(alto * f / 32)) * 32), max(32, int(round(ancho * f / 32)) * 32)
    x = cv2.resize(bgr, (nw, na)).astype(np.float32) / 255.0
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    prob = s.run(None, {s.get_inputs()[0].name: x.transpose(2, 0, 1)[None]})[0]
    prob = np.squeeze(prob).astype(np.float32)

    binario = (prob > umbral).astype(np.uint8)
    contornos, _ = cv2.findContours(binario, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    sx, sy = ancho / nw, alto / na
    cajas, notas = [], []
    for c in contornos:
        if len(c) < 4:
            continue
        (cx, cy), (bw, bh), ang = cv2.minAreaRect(c)
        if min(bw, bh) < 3 or min(bw * sx, bh * sy) > alto_max / 100.0 * max(alto, ancho):
            continue
        x1, y1, cw, ch = cv2.boundingRect(c)
        zona = np.zeros((ch, cw), np.uint8)
        cv2.fillPoly(zona, [c - [x1, y1]], 1)
        nota = float(cv2.mean(prob[y1:y1 + ch, x1:x1 + cw], zona)[0])
        if nota < umbral_caja:
            continue
        # «Unclip» de DBNet: la red predice el núcleo del renglón, hay que agrandarlo
        d = bw * bh * expandir / (2 * (bw + bh) + 1e-6)
        pts = cv2.boxPoints(((cx, cy), (bw + 2 * d, bh + 2 * d), ang))
        pts[:, 0] = np.clip(pts[:, 0] * sx, 0, ancho - 1)
        pts[:, 1] = np.clip(pts[:, 1] * sy, 0, alto - 1)
        cajas.append(pts.astype(np.int32))
        notas.append(nota)

    mascara = np.zeros((alto, ancho), np.uint8)
    if cajas:
        cv2.fillPoly(mascara, cajas, 255)
    hay = len(cajas) >= lineas_min
    datos = {"hay_texto": bool(hay), "lineas": len(cajas),
             "confianza": round(float(np.mean(notas)), 3) if notas else 0.0,
             "cobertura": round(float((mascara > 0).mean()), 4),
             "cajas": [c.tolist() for c in cajas]}
    return datos, dibujar_resultado(imagen, cajas, hay, f"{len(cajas)} lineas"), mascara


@nodo("neuronal/onnx", titulo="Modelo ONNX (imagen → imagen)", entradas={"imagen": IMAGEN},
      salidas={"imagen": IMAGEN}, gpu=True,
      params=[Texto("modelo", "", ayuda="Fichero .onnx en la carpeta «modelos», o ruta absoluta"),
              Entero("tesela", 512, 64, 4096, ayuda="La página se procesa en trozos de este tamaño"),
              Entero("solape", 32, 0, 512, ayuda="Píxeles compartidos entre trozos para que no se noten las juntas"),
              Entero("multiplo", 32, 1, 256, etiqueta="múltiplo",
                     ayuda="Muchas redes exigen que alto y ancho sean múltiplos de 8, 16 o 32"),
              Opcion("rango", "0 a 1", ["0 a 1", "0 a 255", "-1 a 1"], etiqueta="rango de entrada"),
              Logico("salida_probabilidad", False, etiqueta="salida es probabilidad de tinta",
                     ayuda="Para redes de binarización que devuelven 1 = tinta: se invierte a tinta negra")],
      descripcion="Pasa la página por cualquier red de limpieza exportada a ONNX (eliminación de ruido, "
                  "binarización, restauración…). La red debe recibir y devolver imágenes NCHW del mismo tamaño.")
def modelo_onnx(ctx, imagen, modelo, tesela, solape, multiplo, rango, salida_probabilidad):
    if not modelo.strip():
        raise ValueError("Indica el fichero .onnx del modelo")
    s = sesion_onnx(ctx, _ruta_modelo(modelo))
    entrada = s.get_inputs()[0]
    canales = entrada.shape[1] if isinstance(entrada.shape[1], int) else (1 if imagen.ndim == 2 else 3)
    src = gris(imagen)[:, :, None] if canales == 1 else cv2.cvtColor(color(imagen), cv2.COLOR_BGR2RGB)
    alto, ancho = src.shape[:2]
    escalar = {"0 a 1": lambda v: v / 255.0, "0 a 255": lambda v: v, "-1 a 1": lambda v: v / 127.5 - 1.0}[rango]
    volver = {"0 a 1": lambda v: v * 255.0, "0 a 255": lambda v: v, "-1 a 1": lambda v: (v + 1.0) * 127.5}[rango]

    tesela = max(multiplo, tesela // multiplo * multiplo)
    paso = max(multiplo, tesela - 2 * solape)
    suma = pesos = None
    for y in range(0, alto, paso):
        for x in range(0, ancho, paso):
            y0, x0 = max(0, y - solape), max(0, x - solape)
            trozo = src[y0:y0 + tesela, x0:x0 + tesela]
            th, tw = trozo.shape[:2]
            ph, pw = (-th) % multiplo, (-tw) % multiplo
            if ph or pw:
                trozo = cv2.copyMakeBorder(trozo, 0, ph, 0, pw, cv2.BORDER_REFLECT_101 if min(th, tw) > 1
                                           else cv2.BORDER_REPLICATE)
                trozo = trozo[:, :, None] if trozo.ndim == 2 else trozo
            t = escalar(trozo.astype(np.float32)).transpose(2, 0, 1)[None]
            r = s.run(None, {entrada.name: t})[0]
            r = np.asarray(r, np.float32)[0].transpose(1, 2, 0)[:th, :tw]
            if suma is None:
                suma = np.zeros((alto, ancho, r.shape[2]), np.float32)
                pesos = np.zeros((alto, ancho, 1), np.float32)
            # Peso en rampa hacia los bordes del trozo: las juntas se funden
            wy = np.minimum(np.arange(th) + 1, np.arange(th)[::-1] + 1).astype(np.float32)
            wx = np.minimum(np.arange(tw) + 1, np.arange(tw)[::-1] + 1).astype(np.float32)
            w = np.minimum(np.minimum.outer(wy, wx), solape + 1)[:, :, None]
            suma[y0:y0 + th, x0:x0 + tw] += r * w
            pesos[y0:y0 + th, x0:x0 + tw] += w
    res = suma / np.maximum(pesos, 1e-6)
    res = 255.0 - res * 255.0 if salida_probabilidad else volver(res)
    res = np.clip(np.rint(res), 0, 255).astype(np.uint8)
    if res.shape[2] == 1:
        return res[:, :, 0]
    return cv2.cvtColor(res[:, :, :3], cv2.COLOR_RGB2BGR)
