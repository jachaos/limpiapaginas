"""Nodos de entrada y salida del flujo."""
from __future__ import annotations

from pathlib import Path

from ..config import CONFIG
from ..registro import DATOS, IMAGEN, Entero, Logico, Opcion, Texto, nodo
from ..util import escribir_imagen, leer_imagen


@nodo("entrada/pagina", titulo="Página de entrada", salidas={"imagen": IMAGEN},
      params=[Opcion("modo", "original", ["original", "grises", "color"], etiqueta="leer como",
                     ayuda="«grises» ahorra memoria y tiempo si el color no aporta nada")],
      descripcion="La página que se está limpiando: la elegida arriba o, al procesar una carpeta, cada fichero.")
def pagina(ctx, modo):
    if ctx.pagina is None:
        raise ValueError("No hay ninguna página seleccionada")
    return leer_imagen(ctx.pagina, modo)


@nodo("entrada/archivo", titulo="Imagen de archivo", salidas={"imagen": IMAGEN},
      params=[Texto("ruta", "", ayuda="Ruta absoluta, o relativa a la carpeta de entrada"),
              Opcion("modo", "original", ["original", "grises", "color"], etiqueta="leer como")],
      descripcion="Una imagen fija (una máscara, un fondo de referencia…) que no cambia de una página a otra.")
def archivo(ctx, ruta, modo):
    if not ruta.strip():
        raise ValueError("Escribe la ruta de la imagen")
    p = Path(ruta).expanduser()
    if not p.is_absolute():
        p = CONFIG.carpeta_entrada / p
    return leer_imagen(p, modo)


@nodo("salida/guardar", titulo="Guardar página", entradas={"imagen": IMAGEN, "datos": DATOS},
      opcionales=("datos",), salidas={"imagen": IMAGEN},
      params=[Opcion("formato", "png", ["png", "tif", "jpg", "igual que la entrada"]),
              Texto("sufijo", "", ayuda="Se añade al nombre: pagina_limpia.png con el sufijo «_limpia»"),
              Entero("calidad_jpg", 92, 40, 100, etiqueta="calidad JPG"),
              Logico("solo_con_texto", False, etiqueta="solo si hay texto",
                     ayuda="Con «datos» conectado a «¿Hay texto?», no guarda las páginas sin texto")],
      descripcion="Resultado final. Al procesar una carpeta escribe el fichero; en la vista previa solo lo muestra.")
def guardar(ctx, imagen, datos, formato, sufijo, calidad_jpg, solo_con_texto):
    if ctx.lote and ctx.carpeta_salida is not None and ctx.pagina is not None:
        if solo_con_texto and datos is not None and datos.get("hay_texto") is False:
            ctx.informe["guardado"] = "(omitida: sin texto)"
            return imagen
        ext = ctx.pagina.suffix.lower() if formato == "igual que la entrada" else "." + formato
        if any(c in sufijo for c in '\\/:*?"<>|') or ".." in sufijo:
            raise ValueError(f"El sufijo «{sufijo}» tiene caracteres que no valen en un nombre de fichero")
        nombre = ctx.pagina.stem
        if ctx.informe.get("_duplicado"):    # a.png y a.jpg en la misma carpeta: que no se pisen al guardar
            nombre += "_" + ctx.pagina.suffix.lower().lstrip(".")
        destino = Path(ctx.carpeta_salida) / f"{nombre}{sufijo}{ext}"
        if destino.resolve() == Path(ctx.pagina).resolve():
            raise ValueError("El resultado se escribiría encima del original: usa otra carpeta de salida o un sufijo")
        escribir_imagen(destino, imagen, calidad_jpg)
        previo = ctx.informe.get("guardado")
        ctx.informe["guardado"] = f"{previo}; {destino.name}" if previo else destino.name
    return imagen
