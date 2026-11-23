"""Configuración: se lee de ``config.toml`` en la raíz del proyecto."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
EXTENSIONES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")


@dataclass
class Config:
    dispositivo: str = "auto"        # auto | cpu | gpu
    opencl: bool = True              # deja que OpenCV use OpenCL en los filtros que lo admiten
    puerto: int = 8765
    host: str = "127.0.0.1"
    carpeta_entrada: Path = RAIZ / "datos" / "entrada"
    carpeta_salida: Path = RAIZ / "datos" / "salida"
    carpeta_flujos: Path = RAIZ / "flujos"
    carpeta_modelos: Path = RAIZ / "modelos"
    carpeta_nodos_extra: Path = RAIZ / "nodos_extra"
    cache_mb: int = 1024             # memoria máxima para resultados intermedios


def _ruta(valor: str) -> Path:
    p = Path(valor).expanduser()
    return p if p.is_absolute() else RAIZ / p


def cargar(ruta: Path | None = None) -> Config:
    cfg = Config()
    ruta = ruta or RAIZ / "config.toml"
    if ruta.exists():
        datos = tomllib.loads(ruta.read_text(encoding="utf-8"))
        for clave, valor in datos.items():
            if not hasattr(cfg, clave):
                continue
            setattr(cfg, clave, _ruta(valor) if clave.startswith("carpeta_") else valor)
    for carpeta in (cfg.carpeta_entrada, cfg.carpeta_salida, cfg.carpeta_flujos):
        carpeta.mkdir(parents=True, exist_ok=True)
    return cfg


CONFIG = cargar()
