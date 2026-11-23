"""Carga todos los nodos: los de este paquete y los de la carpeta ``nodos_extra``.

Para añadir un proceso nuevo basta con dejar un fichero ``.py`` en
``nodos_extra/`` con una función decorada con ``@nodo`` y reiniciar la
aplicación (o pulsar «Recargar nodos» en la interfaz).
"""
from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
import traceback

from ..config import CONFIG
from ..registro import REGISTRO

ERRORES_CARGA: dict[str, str] = {}


def cargar_todo() -> None:
    ERRORES_CARGA.clear()
    for info in pkgutil.iter_modules(__path__):
        nombre = f"{__name__}.{info.name}"
        try:
            if nombre in sys.modules:
                importlib.reload(sys.modules[nombre])
            else:
                importlib.import_module(nombre)
        except Exception:
            ERRORES_CARGA[info.name] = traceback.format_exc(limit=3)
    carpeta = CONFIG.carpeta_nodos_extra
    if carpeta.is_dir():
        for ruta in sorted(carpeta.glob("*.py")):
            if ruta.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(f"nodos_extra.{ruta.stem}", ruta)
                modulo = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(modulo)
            except Exception:
                ERRORES_CARGA[f"nodos_extra/{ruta.name}"] = traceback.format_exc(limit=3)


def recargar() -> None:
    REGISTRO.clear()
    cargar_todo()


cargar_todo()
