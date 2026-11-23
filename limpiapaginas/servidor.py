"""Servidor local: la interfaz web y la API que usa."""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import Body, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, dispositivo, motor, nodos
from .config import CONFIG, EXTENSIONES
from .lote import Progreso, listar_paginas, procesar_carpeta
from .registro import CATEGORIAS, DATOS, IMAGEN, REGISTRO
from .util import leer_imagen

WEB = Path(__file__).parent / "web"
app = FastAPI(title="LimpiaPáginas", version=__version__)
app.mount("/static", StaticFiles(directory=WEB), name="static")


class Sesion:
    carpeta_entrada: Path = CONFIG.carpeta_entrada
    ultimo: dict[int, motor.Resultado] = {}
    version: int = 0
    lote: Progreso = Progreso()


S = Sesion()


def _nombre_seguro(nombre: str) -> str:
    """Solo un nombre de fichero: sin carpetas ni «..»."""
    limpio = Path(nombre).name
    if not limpio or limpio != nombre or limpio in (".", ".."):
        raise HTTPException(400, "Nombre de fichero no válido")
    return limpio


def _png(img: np.ndarray, maximo: int = 0, jpg: bool = False) -> Response:
    if maximo and max(img.shape[:2]) > maximo:
        f = maximo / max(img.shape[:2])
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    if jpg:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        tipo = "image/jpeg"
    else:
        ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        tipo = "image/png"
    if not ok:
        raise HTTPException(500, "No se pudo codificar la imagen")
    return Response(buf.tobytes(), media_type=tipo, headers={"Cache-Control": "private, max-age=3600"})


def _resumir(valor, profundidad: int = 0):
    """Recorta los datos de un nodo para mostrarlos: sin listas enormes de cajas."""
    if isinstance(valor, dict):
        return {k: _resumir(v, profundidad + 1) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        if len(valor) > 8 or profundidad > 1:
            return f"[{len(valor)} elementos]"
        return [_resumir(v, profundidad + 1) for v in valor]
    if isinstance(valor, (np.integer, np.floating, np.bool_)):
        return valor.item()
    return valor


@app.get("/")
def raiz():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ nodos y dispositivo

@app.get("/api/nodos")
def api_nodos():
    return {"categorias": CATEGORIAS, "nodos": [n.a_json() for n in REGISTRO.values()],
            "errores": nodos.ERRORES_CARGA}


@app.post("/api/nodos/recargar")
def api_recargar():
    with motor.CERROJO:
        nodos.recargar()
        motor.CACHE.vaciar()
    return api_nodos()


@app.get("/api/estado")
def api_estado():
    return {"version": __version__, **dispositivo.estado(), "cache_mb": round(motor.CACHE.mb, 1),
            "carpeta_entrada": str(S.carpeta_entrada), "carpeta_salida": str(CONFIG.carpeta_salida)}


@app.post("/api/dispositivo")
def api_dispositivo(modo: str = Body(..., embed=True)):
    try:
        dispositivo.fijar_modo(modo)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return api_estado()


# ------------------------------------------------------------------ páginas de entrada

@app.get("/api/paginas")
def api_paginas():
    return {"carpeta": str(S.carpeta_entrada),
            "paginas": [p.name for p in listar_paginas(S.carpeta_entrada)] if S.carpeta_entrada.is_dir() else []}


@app.post("/api/carpeta")
def api_carpeta(ruta: str = Body(..., embed=True)):
    p = Path(ruta.strip().strip('"')).expanduser()
    if not p.is_dir():
        raise HTTPException(400, f"No existe la carpeta: {p}")
    S.carpeta_entrada = p
    return api_paginas()


@app.post("/api/subir")
async def api_subir(ficheros: list[UploadFile] = File(...)):
    guardados = []
    S.carpeta_entrada.mkdir(parents=True, exist_ok=True)
    nombres = [Path(f.filename or "").name for f in ficheros]
    for nombre in nombres:      # se valida todo antes de escribir nada
        if Path(nombre).suffix.lower() not in EXTENSIONES:
            raise HTTPException(400, f"Formato no admitido: {nombre}")
    for f, nombre in zip(ficheros, nombres):
        destino, n = S.carpeta_entrada / nombre, 1
        while destino.exists():     # nunca se pisa una página que ya está: «pagina (2).png»
            n += 1
            destino = S.carpeta_entrada / f"{Path(nombre).stem} ({n}){Path(nombre).suffix}"
        destino.write_bytes(await f.read())
        guardados.append(destino.name)
    return {"guardados": guardados, **api_paginas()}


@app.get("/api/pagina/{nombre}")
def api_pagina(nombre: str, max: int = 0):
    ruta = S.carpeta_entrada / _nombre_seguro(nombre)
    if not ruta.is_file():
        raise HTTPException(404, "No existe esa página")
    return _png(leer_imagen(ruta), max, jpg=bool(max))


# ------------------------------------------------------------------ ejecución

@app.post("/api/ejecutar")
def api_ejecutar(flujo: dict = Body(...), pagina: str | None = Body(None)):
    ruta = S.carpeta_entrada / _nombre_seguro(pagina) if pagina else None
    t0 = time.perf_counter()
    try:
        with motor.CERROJO:
            resultados = motor.ejecutar(flujo, ruta)
    except motor.ErrorFlujo as e:
        raise HTTPException(422, str(e))
    S.ultimo = resultados
    S.version += 1
    salida = {}
    for nid, r in resultados.items():
        spec = REGISTRO[next(n["tipo"] for n in flujo["nodos"] if n["id"] == nid)]
        info = {}
        for nombre, tipo in spec.salidas.items():
            v = r.salidas.get(nombre)
            if tipo == IMAGEN and isinstance(v, np.ndarray):
                info[nombre] = {"tipo": IMAGEN, "ancho": v.shape[1], "alto": v.shape[0],
                                "canales": 1 if v.ndim == 2 else v.shape[2]}
            elif tipo == DATOS and v is not None:
                info[nombre] = {"tipo": DATOS, "valor": _resumir(v)}
        salida[str(nid)] = {"estado": r.estado, "ms": round(r.ms, 1), "error": r.error, "avisos": r.avisos,
                            "gpu": r.gpu, "cache": r.de_cache, "clave": r.clave, "salidas": info}
    return {"version": S.version, "ms": round((time.perf_counter() - t0) * 1000, 1), "nodos": salida,
            "cache_mb": round(motor.CACHE.mb, 1)}


@app.get("/api/salida/{nodo_id}/{nombre}")
def api_salida(nodo_id: int, nombre: str, max: int = 0, v: str = ""):
    r = S.ultimo.get(nodo_id)
    img = r.salidas.get(nombre) if r else None
    if not isinstance(img, np.ndarray):
        raise HTTPException(404, "Ese nodo no tiene esa imagen; ejecuta el flujo")
    return _png(img, max, jpg=bool(max) and max <= 512)


# ------------------------------------------------------------------ flujos guardados

def _ruta_flujo(nombre: str) -> Path:
    limpio = re.sub(r"[^\w\- ().,+áéíóúñüÁÉÍÓÚÑÜ]", "", nombre).strip()
    if not limpio:
        raise HTTPException(400, "Nombre de flujo no válido")
    return CONFIG.carpeta_flujos / f"{limpio}.json"


@app.get("/api/flujos")
def api_flujos():
    lista = []
    for p in sorted(CONFIG.carpeta_flujos.glob("*.json")):
        try:
            f = motor.cargar_flujo(p)
            lista.append({"nombre": p.stem, "descripcion": f.get("descripcion", ""), "nodos": len(f.get("nodos", []))})
        except Exception:
            lista.append({"nombre": p.stem, "descripcion": "(fichero dañado)", "nodos": 0})
    return {"flujos": lista}


@app.get("/api/flujos/{nombre}")
def api_flujo(nombre: str):
    ruta = _ruta_flujo(nombre)
    if not ruta.is_file():
        raise HTTPException(404, "No existe ese flujo")
    return motor.cargar_flujo(ruta)


@app.put("/api/flujos/{nombre}")
def api_guardar_flujo(nombre: str, flujo: dict = Body(...)):
    try:
        motor.validar(flujo)
    except motor.ErrorFlujo as e:
        raise HTTPException(422, str(e))
    ruta = _ruta_flujo(nombre)
    flujo["nombre"] = ruta.stem
    motor.guardar_flujo(flujo, ruta)
    return {"guardado": ruta.stem}


@app.delete("/api/flujos/{nombre}")
def api_borrar_flujo(nombre: str):
    ruta = _ruta_flujo(nombre)
    if ruta.is_file():
        ruta.unlink()
    return {"borrado": ruta.stem}


# ------------------------------------------------------------------ lotes

_CERROJO_LOTE = threading.Lock()


@app.post("/api/lote")
def api_lote(flujo: dict = Body(...), entrada: str = Body(""), salida: str = Body(""),
             recursivo: bool = Body(False)):
    c_entrada = Path(entrada.strip().strip('"')).expanduser() if entrada.strip() else S.carpeta_entrada
    c_salida = Path(salida.strip().strip('"')).expanduser() if salida.strip() else CONFIG.carpeta_salida
    if c_entrada.resolve() == c_salida.resolve():
        raise HTTPException(400, "La carpeta de salida no puede ser la de entrada: se pisarían los originales")
    try:
        motor.validar(flujo)
        if not c_entrada.is_dir():
            raise motor.ErrorFlujo(f"No existe la carpeta de entrada: {c_entrada}")
        if not listar_paginas(c_entrada, recursivo):
            raise motor.ErrorFlujo(f"No hay imágenes en {c_entrada}")
    except motor.ErrorFlujo as e:
        raise HTTPException(422, str(e))
    with _CERROJO_LOTE:     # dos peticiones a la vez no deben arrancar dos lotes
        if S.lote.activo:
            raise HTTPException(409, "Ya hay un lote en marcha")
        progreso = S.lote = Progreso(activo=True, inicio=time.time(), carpeta_salida=str(c_salida))

    def trabajo():
        try:
            procesar_carpeta(flujo, c_entrada, c_salida, recursivo=recursivo, progreso=progreso)
        except Exception as e:
            progreso.activo, progreso.fin = False, time.time()
            progreso.mensaje = f"Error: {e}"

    threading.Thread(target=trabajo, daemon=True).start()
    return progreso.a_json()


@app.get("/api/lote")
def api_lote_estado():
    return S.lote.a_json()


@app.post("/api/lote/cancelar")
def api_lote_cancelar():
    S.lote.cancelar = True
    return S.lote.a_json()
