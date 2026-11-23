"""Motor de ejecución de flujos.

Un flujo es un grafo dirigido de nodos. Formato en disco (JSON)::

    {"version": 1, "nombre": "…",
     "nodos":   [{"id": 1, "tipo": "entrada/pagina", "pos": [40, 80], "params": {…}, "activo": true}],
     "enlaces": [[1, "imagen", 2, "imagen"]]}      # [origen, salida, destino, entrada]

El motor ordena los nodos, ejecuta cada uno cuando tiene sus entradas y guarda
los resultados intermedios en una caché: al mover un parámetro solo se recalcula
desde ese nodo hacia delante.
"""
from __future__ import annotations

import hashlib
import os
import json
import threading
import time
import traceback
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import dispositivo
from .config import CONFIG
from .registro import DATOS, IMAGEN, REGISTRO


class ErrorFlujo(Exception):
    """El flujo está mal formado (ciclos, tipos de nodo desconocidos…)."""


@dataclass
class Contexto:
    """Lo que recibe cada nodo como primer argumento."""
    pagina: Path | None = None            # fichero que se está limpiando
    lote: bool = False                    # True al procesar una carpeta: los nodos de salida escriben a disco
    carpeta_salida: Path | None = None
    nodo_id: int | None = None
    uso_gpu: bool = False
    avisos: list[str] = field(default_factory=list)
    informe: dict[str, Any] = field(default_factory=dict)   # columnas extra para el informe del lote

    def avisar(self, mensaje: str) -> None:
        self.avisos.append(mensaje)


@dataclass
class Resultado:
    estado: str = "ok"                    # ok | error | omitido | desactivado
    salidas: dict[str, Any] = field(default_factory=dict)
    ms: float = 0.0
    error: str = ""
    avisos: list[str] = field(default_factory=list)
    gpu: bool = False
    de_cache: bool = False
    clave: str = ""


class Cache:
    """Caché LRU de resultados de nodo, limitada por memoria."""

    def __init__(self, max_mb: int = 1024):
        self.max_bytes = max_mb * 2**20
        self._datos: OrderedDict[str, tuple[Resultado, int]] = OrderedDict()
        self._bytes = 0

    @staticmethod
    def _peso(salidas: dict) -> int:
        return sum(v.nbytes for v in salidas.values() if isinstance(v, np.ndarray)) + 256

    def obtener(self, clave: str) -> Resultado | None:
        par = self._datos.get(clave)
        if par is None:
            return None
        self._datos.move_to_end(clave)
        return par[0]

    def guardar(self, clave: str, resultado: Resultado) -> None:
        peso = self._peso(resultado.salidas)
        if peso > self.max_bytes:
            return
        if clave in self._datos:
            self._bytes -= self._datos.pop(clave)[1]
        self._datos[clave] = (resultado, peso)
        self._bytes += peso
        while self._bytes > self.max_bytes and self._datos:
            _, (_, p) = self._datos.popitem(last=False)
            self._bytes -= p

    def vaciar(self) -> None:
        self._datos.clear()
        self._bytes = 0

    @property
    def mb(self) -> float:
        return self._bytes / 2**20


CACHE = Cache(CONFIG.cache_mb)
CERROJO = threading.Lock()   # un flujo a la vez: OpenCV y la GPU ya paralelizan por dentro


def _hash(*partes) -> str:
    h = hashlib.sha1()
    for p in partes:
        h.update(json.dumps(p, sort_keys=True, ensure_ascii=False, default=str).encode())
        h.update(b"|")
    return h.hexdigest()[:20]


def firma_pagina(pagina: Path | None) -> str:
    if pagina is None:
        return "sin-pagina"
    try:
        st = pagina.stat()
        return f"{pagina}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        return str(pagina)


def normalizar_imagen(valor) -> np.ndarray:
    """Deja cualquier resultado de imagen como uint8 contiguo, HxW o HxWx3."""
    valor = dispositivo.mat(valor)
    if not isinstance(valor, np.ndarray):
        raise TypeError(f"se esperaba una imagen y llegó {type(valor).__name__}")
    if valor.dtype == np.bool_:
        valor = valor.astype(np.uint8) * 255
    elif valor.dtype != np.uint8:
        valor = np.clip(np.rint(valor), 0, 255).astype(np.uint8)
    if valor.ndim == 3 and valor.shape[2] == 1:
        valor = valor[:, :, 0]
    if valor.ndim == 3 and valor.shape[2] == 4:
        valor = valor[:, :, :3]
    if valor.ndim not in (2, 3):
        raise TypeError(f"forma de imagen no válida: {valor.shape}")
    return np.ascontiguousarray(valor)


def validar(flujo: dict) -> tuple[dict[int, dict], dict[int, dict[str, tuple[int, str]]]]:
    """Comprueba el flujo y devuelve (nodos por id, entradas conectadas por nodo)."""
    nodos: dict[int, dict] = {}
    for n in flujo.get("nodos", []):
        if n.get("tipo") not in REGISTRO:
            raise ErrorFlujo(f"Tipo de nodo desconocido: {n.get('tipo')!r} (nodo {n.get('id')})")
        if n["id"] in nodos:
            raise ErrorFlujo(f"Identificador de nodo repetido: {n['id']}")
        nodos[n["id"]] = n
    conexiones: dict[int, dict[str, tuple[int, str]]] = {i: {} for i in nodos}
    for enlace in flujo.get("enlaces", []):
        origen, salida, destino, entrada = enlace
        if origen not in nodos or destino not in nodos:
            continue   # enlace colgando de un nodo borrado: se ignora
        spec_o, spec_d = REGISTRO[nodos[origen]["tipo"]], REGISTRO[nodos[destino]["tipo"]]
        if salida not in spec_o.salidas or entrada not in spec_d.entradas:
            continue   # el nodo cambió de forma desde que se guardó el flujo
        if spec_o.salidas[salida] != spec_d.entradas[entrada]:
            raise ErrorFlujo(f"No se puede conectar «{salida}» ({spec_o.salidas[salida]}) de "
                             f"{spec_o.titulo} con «{entrada}» ({spec_d.entradas[entrada]}) de {spec_d.titulo}")
        conexiones[destino][entrada] = (origen, salida)
    return nodos, conexiones


def orden_topologico(nodos: dict[int, dict], conexiones: dict) -> list[int]:
    pendientes = {i: {o for o, _ in conexiones[i].values()} for i in nodos}
    orden: list[int] = []
    libres = sorted(i for i, deps in pendientes.items() if not deps)
    while libres:
        actual = libres.pop(0)
        orden.append(actual)
        for i, deps in pendientes.items():
            if actual in deps:
                deps.discard(actual)
                if not deps and i not in orden and i not in libres:
                    libres.append(i)
        libres.sort()
    if len(orden) != len(nodos):
        raise ErrorFlujo("El flujo tiene un ciclo: una salida vuelve a entrar en un nodo anterior")
    return orden


def ejecutar(flujo: dict, pagina: Path | None = None, *, lote: bool = False,
             carpeta_salida: Path | None = None, cache: Cache | None = CACHE,
             informe: dict | None = None) -> dict[int, Resultado]:
    """Ejecuta el flujo completo sobre una página y devuelve el resultado de cada nodo."""
    nodos, conexiones = validar(flujo)
    orden = orden_topologico(nodos, conexiones)
    firma = firma_pagina(pagina)
    resultados: dict[int, Resultado] = {}

    for nid in orden:
        n = nodos[nid]
        spec = REGISTRO[n["tipo"]]
        params = spec.normalizar_params(n.get("params"))
        entradas: dict[str, Any] = {}
        claves_entrada: list[str] = []
        fallo = ""

        for nombre in spec.entradas:
            con = conexiones[nid].get(nombre)
            if con is None:
                if nombre not in spec.opcionales:
                    fallo = f"falta conectar la entrada «{nombre}»"
                entradas[nombre] = None
                claves_entrada.append("-")
                continue
            previo = resultados[con[0]]
            if previo.estado in ("error", "omitido"):
                fallo = "un nodo anterior no se pudo ejecutar"
                break
            entradas[nombre] = previo.salidas.get(con[1])
            claves_entrada.append(f"{previo.clave}:{con[1]}")

        if fallo:
            resultados[nid] = Resultado(estado="omitido", error=fallo)
            continue

        activo = n.get("activo", True)
        clave = _hash(spec.tipo, params, claves_entrada, activo, dispositivo.usar_gpu() if spec.gpu else None,
                      firma if not spec.entradas else None)

        if not activo:
            # Nodo desactivado: deja pasar la primera imagen de entrada tal cual
            paso = next((v for k, v in entradas.items() if spec.entradas[k] == IMAGEN and v is not None), None)
            salidas = {k: (paso if t == IMAGEN else {}) for k, t in spec.salidas.items()}
            resultados[nid] = Resultado(estado="desactivado", salidas=salidas, clave=clave)
            continue

        usar_cache = cache is not None and spec.cache and not (lote and spec.categoria == "salida")
        if usar_cache:
            previo = cache.obtener(clave)
            if previo is not None:
                resultados[nid] = Resultado(previo.estado, previo.salidas, previo.ms, previo.error,
                                            previo.avisos, previo.gpu, True, clave)
                continue

        ctx = Contexto(pagina=pagina, lote=lote, carpeta_salida=carpeta_salida, nodo_id=nid,
                       informe=informe if informe is not None else {})
        t0 = time.perf_counter()
        try:
            crudo = spec.funcion(ctx, **entradas, **params)
            nombres = list(spec.salidas)
            if len(nombres) == 0:
                crudo = ()
            elif len(nombres) == 1:
                crudo = (crudo,)
            if len(crudo) != len(nombres):
                raise TypeError(f"el nodo devolvió {len(crudo)} valores y declara {len(nombres)} salidas")
            salidas = {}
            for nombre, valor in zip(nombres, crudo):
                if spec.salidas[nombre] == IMAGEN:
                    valor = normalizar_imagen(valor)
                elif spec.salidas[nombre] == DATOS and not isinstance(valor, dict):
                    valor = {"valor": valor}
                salidas[nombre] = valor
            res = Resultado("ok", salidas, (time.perf_counter() - t0) * 1000, "", ctx.avisos, ctx.uso_gpu,
                            False, clave)
            if usar_cache:
                cache.guardar(clave, res)
        except Exception as e:
            traza = traceback.format_exc(limit=4)
            res = Resultado("error", {}, (time.perf_counter() - t0) * 1000,
                            f"{type(e).__name__}: {e}", ctx.avisos + [traza], False, False, clave)
        resultados[nid] = res
    return resultados


# ---------------------------------------------------------------- utilidades para crear flujos por código

def cadena(*pasos, nombre: str = "", descripcion: str = "") -> dict:
    """Crea un flujo lineal: cada paso es ``"tipo"`` o ``("tipo", {params})``.

    Conecta la primera salida de imagen de cada nodo con la primera entrada de
    imagen del siguiente. Útil para pruebas y para generar flujos de partida.
    """
    nodos, enlaces = [], []
    previo = None
    for i, paso in enumerate(pasos, start=1):
        tipo, params = (paso, {}) if isinstance(paso, str) else paso
        spec = REGISTRO[tipo]
        nodos.append({"id": i, "tipo": tipo, "pos": [40 + 260 * ((i - 1) % 5), 60 + 330 * ((i - 1) // 5)],
                      "params": spec.normalizar_params(params), "activo": True})
        if previo is not None:
            spec_p = REGISTRO[nodos[previo - 1]["tipo"]]
            salida = next(k for k, t in spec_p.salidas.items() if t == IMAGEN)
            entrada = next(k for k, t in spec.entradas.items() if t == IMAGEN)
            enlaces.append([previo, salida, i, entrada])
        previo = i
    return {"version": 1, "nombre": nombre, "descripcion": descripcion, "nodos": nodos, "enlaces": enlaces}


def cargar_flujo(ruta: Path) -> dict:
    return json.loads(Path(ruta).read_text(encoding="utf-8"))


def guardar_flujo(flujo: dict, ruta: Path) -> None:
    # a un temporal y se renombra: un corte a medias no deja el flujo truncado
    ruta = Path(ruta)
    temporal = ruta.with_name(ruta.name + ".tmp")
    temporal.write_text(json.dumps(flujo, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporal, ruta)
