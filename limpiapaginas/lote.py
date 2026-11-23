"""Procesado por lotes: aplica un flujo a todas las páginas de una carpeta."""
from __future__ import annotations

import csv
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import motor
from .config import EXTENSIONES
from .registro import DATOS, REGISTRO


@dataclass
class Progreso:
    activo: bool = False
    total: int = 0
    hechas: int = 0
    errores: int = 0
    actual: str = ""
    inicio: float = 0.0
    fin: float = 0.0
    mensaje: str = ""
    carpeta_salida: str = ""
    cancelar: bool = False
    ultimos_errores: list[str] = field(default_factory=list)

    def a_json(self) -> dict:
        transcurrido = (self.fin or time.time()) - self.inicio if self.inicio else 0
        return {"activo": self.activo, "total": self.total, "hechas": self.hechas, "errores": self.errores,
                "actual": self.actual, "segundos": round(transcurrido, 1), "mensaje": self.mensaje,
                "carpeta_salida": self.carpeta_salida, "ultimos_errores": self.ultimos_errores[-5:],
                "paginas_por_minuto": round(self.hechas / transcurrido * 60, 1) if transcurrido > 0 else 0}


def listar_paginas(carpeta: Path, recursivo: bool = False) -> list[Path]:
    patron = "**/*" if recursivo else "*"
    return sorted(p for p in Path(carpeta).glob(patron) if p.is_file() and p.suffix.lower() in EXTENSIONES)


def _columnas_datos(flujo: dict, resultados: dict) -> dict:
    """Aplana las salidas de tipo «datos» en columnas ``Título del nodo.clave``."""
    fila = {}
    for n in flujo["nodos"]:
        spec, res = REGISTRO[n["tipo"]], resultados.get(n["id"])
        if res is None or res.estado != "ok":
            continue
        for nombre, tipo in spec.salidas.items():
            if tipo != DATOS:
                continue
            for clave, valor in (res.salidas.get(nombre) or {}).items():
                if isinstance(valor, (bool, int, float, str)):
                    fila[f"{spec.titulo}.{clave}"] = valor
    return fila


def procesar_carpeta(flujo: dict, entrada: Path, salida: Path, *, recursivo: bool = False,
                     progreso: Progreso | None = None, al_avanzar: Callable[[Progreso], None] | None = None) -> Progreso:
    """Ejecuta el flujo sobre cada imagen de ``entrada`` y escribe los resultados en ``salida``.

    Deja en ``salida`` un ``informe.csv`` (una fila por página, con los datos de
    los nodos de detección) y una copia del flujo usado.
    """
    progreso = progreso or Progreso()
    entrada, salida = Path(entrada), Path(salida)
    motor.validar(flujo)
    if not any(n["tipo"].startswith("salida/") and n.get("activo", True) for n in flujo["nodos"]):
        raise motor.ErrorFlujo("El flujo no tiene ningún nodo «Guardar página» activo: no se escribiría nada")
    if not entrada.is_dir():
        raise motor.ErrorFlujo(f"No existe la carpeta de entrada: {entrada}")
    e_real, s_real = entrada.resolve(), salida.resolve()
    if e_real == s_real or (salida.exists() and os.path.samefile(entrada, salida)):
        raise motor.ErrorFlujo("La carpeta de salida no puede ser la de entrada: se pisarían los originales")
    # con subcarpetas, una salida dentro de la entrada se volvería a procesar en la siguiente pasada
    paginas = [p for p in listar_paginas(entrada, recursivo) if s_real not in p.resolve().parents]
    vistos = Counter((p.parent, p.stem.lower()) for p in paginas)
    salida.mkdir(parents=True, exist_ok=True)
    motor.guardar_flujo(flujo, salida / "flujo_usado.json")

    progreso.activo, progreso.total, progreso.hechas, progreso.errores = True, len(paginas), 0, 0
    progreso.inicio, progreso.fin, progreso.cancelar = time.time(), 0.0, False
    progreso.carpeta_salida, progreso.mensaje, progreso.ultimos_errores = str(salida), "", []
    filas: list[dict] = []
    try:
        for pagina in paginas:
            if progreso.cancelar:
                progreso.mensaje = "Cancelado"
                break
            relativa = pagina.relative_to(entrada)
            progreso.actual = str(relativa)
            informe: dict = {"_duplicado": vistos[(pagina.parent, pagina.stem.lower())] > 1}
            t0 = time.perf_counter()
            fila = {"fichero": str(relativa), "estado": "ok", "ms": 0, "guardado": "", "error": ""}
            try:
                with motor.CERROJO:
                    resultados = motor.ejecutar(flujo, pagina, lote=True, carpeta_salida=salida / relativa.parent,
                                                cache=None, informe=informe)
                fallos = [f"{REGISTRO[n['tipo']].titulo}: {resultados[n['id']].error}" for n in flujo["nodos"]
                          if resultados[n["id"]].estado in ("error", "omitido")]
                if fallos:
                    fila["estado"], fila["error"] = "error", " | ".join(fallos)
                fila.update(_columnas_datos(flujo, resultados))
            except Exception as e:
                fila["estado"], fila["error"] = "error", f"{type(e).__name__}: {e}"
            fila["ms"] = round((time.perf_counter() - t0) * 1000)
            fila["guardado"] = informe.get("guardado", "")
            if fila["estado"] == "error":
                progreso.errores += 1
                progreso.ultimos_errores.append(f"{relativa}: {fila['error']}")
            filas.append(fila)
            progreso.hechas += 1
            if al_avanzar:
                al_avanzar(progreso)
    finally:
        columnas: list[str] = []
        for f in filas:
            columnas += [c for c in f if c not in columnas]
        with open(salida / "informe.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=columnas, delimiter=";")
            w.writeheader()
            w.writerows(filas)
        progreso.activo, progreso.fin, progreso.actual = False, time.time(), ""
        if not progreso.mensaje:
            progreso.mensaje = "Terminado"
    return progreso
