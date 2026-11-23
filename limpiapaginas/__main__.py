"""Línea de órdenes: ``python -m limpiapaginas [servidor|ejecutar|diagnostico|nodos]``."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _servidor(args) -> int:
    import threading
    import webbrowser

    import uvicorn

    from .config import CONFIG
    host, puerto = args.host or CONFIG.host, args.puerto or CONFIG.puerto
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{puerto}"
    print(f"LimpiaPáginas en {url}  (Ctrl+C para salir)")
    if not args.sin_navegador:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run("limpiapaginas.servidor:app", host=host, port=puerto, log_level="warning")
    return 0


def _ejecutar(args) -> int:
    from . import motor, nodos  # noqa: F401  (carga los nodos)
    from .lote import Progreso, procesar_carpeta
    flujo = motor.cargar_flujo(Path(args.flujo))

    def avance(p: Progreso):
        print(f"\r  {p.hechas}/{p.total}  errores: {p.errores}  {p.actual[:50]:50s}", end="", flush=True)

    try:
        p = procesar_carpeta(flujo, Path(args.entrada), Path(args.salida), recursivo=args.recursivo,
                             al_avanzar=avance)
    except motor.ErrorFlujo as e:
        print(f"Error: {e}")
        return 2
    info = p.a_json()
    print(f"\n{p.mensaje}: {p.hechas} páginas en {info['segundos']} s "
          f"({info['paginas_por_minuto']} págs./min), {p.errores} con error.")
    print(f"Resultados e informe.csv en {args.salida}")
    return 1 if p.errores else 0


def _nodos(args) -> int:
    from . import nodos
    from .registro import CATEGORIAS, REGISTRO
    for cat, nombre in CATEGORIAS.items():
        lista = [n for n in REGISTRO.values() if n.categoria == cat]
        if lista:
            print(f"\n{nombre}")
            for n in lista:
                print(f"  {n.tipo:32s} {n.titulo}{'  [GPU]' if n.gpu else ''}")
    otras = [n for n in REGISTRO.values() if n.categoria not in CATEGORIAS]
    if otras:
        print("\nOtros")
        for n in otras:
            print(f"  {n.tipo:32s} {n.titulo}")
    for origen, error in nodos.ERRORES_CARGA.items():
        print(f"\n[ERROR] no se pudo cargar {origen}:\n{error}")
    return 0


def _diagnostico(args) -> int:
    import cv2
    import numpy as np

    from . import dispositivo, motor, nodos
    from .config import CONFIG, RAIZ
    from .registro import REGISTRO

    def linea(ok, texto):
        print(f"  [{'OK' if ok else '--'}] {texto}")

    e = dispositivo.estado()
    print(f"\nLimpiaPáginas - diagnóstico   (Python {sys.version.split()[0]}, proyecto en {RAIZ})\n")
    linea(True, f"OpenCV {e['opencv']}" + (" con módulos contrib" if e["opencv_contrib"] else ""))
    linea(e["opencv_contrib"], "opencv-contrib (binarización Wolf/NICK/Niblack)")
    linea(e["opencl_disponible"], f"OpenCL para OpenCV: {e['opencl_dispositivo'] or 'no disponible'}")
    linea(e["torch"] is not None, f"PyTorch {e['torch'] or 'no instalado'}")
    linea(e["cuda"], f"CUDA en PyTorch: {e['gpu_nombre'] or 'no disponible'}"
          + (f" ({e['gpu_vram_libre_mb']} MB libres de {e['gpu_vram_mb']})" if e["gpu_vram_mb"] else ""))
    linea(e["onnxruntime"] is not None, f"ONNX Runtime {e['onnxruntime'] or 'no instalado'}: "
          + ", ".join(e["onnx_proveedores"]))
    linea("CUDAExecutionProvider" in e["onnx_proveedores"], "ONNX Runtime con GPU (CUDAExecutionProvider)")
    linea(not nodos.ERRORES_CARGA, f"{len(REGISTRO)} nodos cargados"
          + (f"; con errores: {', '.join(nodos.ERRORES_CARGA)}" if nodos.ERRORES_CARGA else ""))
    modelo = CONFIG.carpeta_modelos / "PP-OCRv6_det_small.onnx"
    linea(modelo.exists(), f"Modelo detector de texto: {modelo.name}")

    print("\nPrueba de velocidad (página de 3500 x 5000 px, grises):")
    rng = np.random.default_rng(0)
    pagina = np.clip(rng.normal(215, 10, (5000, 3500)), 0, 255).astype(np.uint8)
    for y in range(300, 4700, 70):
        cv2.putText(pagina, "Texto de prueba para medir la velocidad de limpieza 0123456789", (250, y),
                    cv2.FONT_HERSHEY_COMPLEX, 1.6, 30, 3)
    pruebas = [("fondo/aplanar", {}), ("ruido/gaussiano", {"tam": 9}), ("binarizar/local", {}),
               ("neuronal/detector_texto", {})]
    modos = ["cpu"] + (["gpu"] if e["cuda"] or "CUDAExecutionProvider" in e["onnx_proveedores"] else [])
    todo_ok = True
    for tipo, params in pruebas:
        spec, tiempos = REGISTRO[tipo], []
        for m in modos:
            dispositivo.fijar_modo(m)
            ctx = motor.Contexto()
            try:
                spec.funcion(ctx, pagina, **spec.normalizar_params(params))      # calentamiento
                ctx = motor.Contexto()
                t0 = time.perf_counter()
                spec.funcion(ctx, pagina, **spec.normalizar_params(params))
                ms = (time.perf_counter() - t0) * 1000
                real = "gpu" if ctx.uso_gpu else "cpu"
                aviso = f" (pedido {m}, se ejecutó en {real}: {ctx.avisos[0]})" if ctx.avisos else ""
                tiempos.append(f"{m.upper()} {ms:7.0f} ms{aviso}")
            except Exception as ex:
                todo_ok = False
                tiempos.append(f"{m.upper()} ERROR {type(ex).__name__}: {ex}")
        print(f"  {spec.titulo:36s} " + "   ".join(tiempos))
    dispositivo.fijar_modo(CONFIG.dispositivo)
    if not e["cuda"]:
        print("\nSin GPU para PyTorch: todo funciona en CPU. Si este equipo tiene una NVIDIA, revisa el "
              "controlador y vuelve a ejecutar el instalador.")
    print()
    return 0 if todo_ok else 1


def main(argv=None) -> int:
    for flujo_salida in (sys.stdout, sys.stderr):
        try:
            flujo_salida.reconfigure(errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(prog="limpiapaginas", description="Limpieza de páginas antes del OCR")
    sub = p.add_subparsers(dest="orden")
    s = sub.add_parser("servidor", help="Abre la interfaz visual (por defecto)")
    s.add_argument("--host"), s.add_argument("--puerto", type=int)
    s.add_argument("--sin-navegador", action="store_true")
    e = sub.add_parser("ejecutar", help="Aplica un flujo guardado a una carpeta, sin interfaz")
    e.add_argument("flujo"), e.add_argument("entrada"), e.add_argument("salida")
    e.add_argument("--recursivo", action="store_true", help="Incluye las subcarpetas")
    sub.add_parser("diagnostico", help="Comprueba GPU, librerías y velocidad")
    sub.add_parser("nodos", help="Lista los procesos disponibles")
    args = p.parse_args(argv)
    if args.orden is None:
        args = p.parse_args(["servidor"])
    return {"servidor": _servidor, "ejecutar": _ejecutar, "diagnostico": _diagnostico, "nodos": _nodos}[args.orden](args)


if __name__ == "__main__":
    sys.exit(main())
