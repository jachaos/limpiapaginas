"""Detección y uso de la GPU.

Hay tres vías de aceleración, y las tres son opcionales:

* **PyTorch + CUDA**: para los nodos marcados ``gpu=True`` y las redes neuronales.
* **ONNX Runtime + CUDA**: para los modelos ``.onnx`` (detector de texto, modelos propios).
* **OpenCL en OpenCV** (``cv2.UMat``): acelera algunos filtros clásicos sin compilar nada.

Si algo falla en la GPU (falta memoria porque otro proceso la ocupa, por
ejemplo), el nodo se repite en CPU y el aviso queda en el resultado.
"""
from __future__ import annotations

import functools
import logging

import cv2
import numpy as np

from .config import CONFIG

log = logging.getLogger("limpiapaginas")

_modo = CONFIG.dispositivo if CONFIG.dispositivo in ("auto", "cpu", "gpu") else "auto"


def modo() -> str:
    return _modo


def fijar_modo(nuevo: str) -> None:
    """Cambia el dispositivo en caliente: ``auto``, ``cpu`` o ``gpu``."""
    global _modo
    if nuevo not in ("auto", "cpu", "gpu"):
        raise ValueError("dispositivo debe ser auto, cpu o gpu")
    _modo = nuevo
    cv2.ocl.setUseOpenCL(CONFIG.opencl and nuevo != "cpu" and cv2.ocl.haveOpenCL())


@functools.lru_cache(maxsize=1)
def _torch():
    try:
        import torch
        return torch
    except Exception as e:  # no instalado o instalación rota
        log.info("PyTorch no disponible: %s", e)
        return None


@functools.lru_cache(maxsize=1)
def cuda_disponible() -> bool:
    t = _torch()
    try:
        return bool(t and t.cuda.is_available())
    except Exception:
        return False


def usar_gpu() -> bool:
    """¿Deben los nodos con versión GPU usarla ahora?"""
    if _modo == "cpu":
        return False
    return cuda_disponible()


def torch_device():
    t = _torch()
    return t.device("cuda" if usar_gpu() else "cpu")


def opencl_activo() -> bool:
    return bool(CONFIG.opencl and _modo != "cpu" and cv2.ocl.haveOpenCL() and cv2.ocl.useOpenCL())


def umat(img: np.ndarray):
    """Sube la imagen a la GPU por OpenCL si está activo; si no, la deja como está."""
    return cv2.UMat(img) if opencl_activo() else img


def mat(x) -> np.ndarray:
    """Devuelve siempre un ``numpy.ndarray`` (baja de la GPU si era ``cv2.UMat``)."""
    return x.get() if isinstance(x, cv2.UMat) else x


def proveedores_onnx() -> list[str]:
    """Proveedores de ONNX Runtime en orden de preferencia según el dispositivo elegido."""
    try:
        import onnxruntime as ort
    except Exception:
        return []
    disponibles = ort.get_available_providers()
    if _modo != "cpu" and "CUDAExecutionProvider" in disponibles:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def a_tensor(img: np.ndarray, device=None):
    """Imagen uint8 (HxW o HxWxC) -> tensor float32 1xCxHxW en [0, 255]."""
    t = _torch()
    x = t.from_numpy(np.ascontiguousarray(img)).to(device or torch_device())
    x = x[None, None] if x.ndim == 2 else x.permute(2, 0, 1)[None]
    return x.float()


def a_imagen(x) -> np.ndarray:
    """Tensor 1xCxHxW -> imagen uint8 (HxW si C=1, HxWxC si no)."""
    x = x.clamp(0, 255).round().byte()[0]
    x = x[0] if x.shape[0] == 1 else x.permute(1, 2, 0)
    return np.ascontiguousarray(x.cpu().numpy())


def con_gpu(ctx, en_gpu, en_cpu):
    """Ejecuta ``en_gpu()`` si toca y hay GPU; ante cualquier fallo, ``en_cpu()``.

    ``ctx.uso_gpu`` queda a True cuando el resultado salió de la GPU, para que
    la interfaz lo muestre en el nodo.
    """
    if usar_gpu():
        try:
            r = en_gpu()
            ctx.uso_gpu = True
            return r
        except Exception as e:
            ctx.avisar(f"GPU no disponible para este nodo ({type(e).__name__}: {e}); se usó la CPU")
            t = _torch()
            if t is not None:
                try:
                    t.cuda.empty_cache()
                except Exception:
                    pass
    return en_cpu()


def estado() -> dict:
    """Resumen del hardware y de las librerías, para la interfaz y el diagnóstico."""
    info = {
        "modo": _modo,
        "gpu_en_uso": usar_gpu(),
        "opencv": cv2.__version__,
        "opencv_contrib": hasattr(cv2, "ximgproc"),
        "opencl_disponible": bool(cv2.ocl.haveOpenCL()),
        "opencl_activo": opencl_activo(),
        "opencl_dispositivo": None,
        "torch": None, "cuda": False, "gpu_nombre": None, "gpu_vram_mb": None, "gpu_vram_libre_mb": None,
        "onnxruntime": None, "onnx_proveedores": [],
    }
    if info["opencl_disponible"]:
        try:
            info["opencl_dispositivo"] = cv2.ocl.Device.getDefault().name()
        except Exception:
            pass
    t = _torch()
    if t is not None:
        info["torch"] = t.__version__
        info["cuda"] = cuda_disponible()
        if info["cuda"]:
            try:
                info["gpu_nombre"] = t.cuda.get_device_name(0)
                libre, total = t.cuda.mem_get_info(0)
                info["gpu_vram_mb"] = int(total / 2**20)
                info["gpu_vram_libre_mb"] = int(libre / 2**20)
            except Exception as e:
                info["gpu_nombre"] = f"(error al consultar: {e})"
    try:
        import onnxruntime as ort
        info["onnxruntime"] = ort.__version__
        info["onnx_proveedores"] = ort.get_available_providers()
    except Exception:
        pass
    return info


fijar_modo(_modo)
