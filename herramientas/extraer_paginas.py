"""Saca páginas de muestra de los PDF de una carpeta como imágenes PNG.

Uso:  python herramientas/extraer_paginas.py muestras datos/entrada_muestras --por-pdf 4

Si la página es un escaneo (una sola imagen incrustada) se extrae esa imagen
tal cual, sin remuestrear; si no, se dibuja la página a los ppp indicados.
Necesita PyMuPDF (``pip install pymupdf``).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf


def paginas_repartidas(total: int, cuantas: int) -> list[int]:
    """Índices repartidos por todo el documento, evitando cubierta y contracubierta."""
    if total <= cuantas:
        return list(range(total))
    return sorted({int((i + 0.5) * total / cuantas) for i in range(cuantas)})


def extraer(pdf: Path, destino: Path, cuantas: int, ppp: int) -> int:
    hechas = 0
    with pymupdf.open(pdf) as doc:
        for n in paginas_repartidas(len(doc), cuantas):
            pagina = doc[n]
            imagenes = pagina.get_images(full=True)
            if len(imagenes) == 1:
                pix = pymupdf.Pixmap(doc, imagenes[0][0])
                if pix.colorspace is None or pix.colorspace.n > 3 or pix.alpha:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                origen = "incrustada"
            else:
                pix = pagina.get_pixmap(dpi=ppp)
                origen = f"{ppp} ppp"
            salida = destino / f"{pdf.stem}_p{n + 1:04d}.png"
            pix.save(salida)
            print(f"  {salida.name}  {pix.width}x{pix.height}  ({origen})")
            hechas += 1
    return hechas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("origen", type=Path, help="Carpeta con PDF o un PDF suelto")
    ap.add_argument("destino", type=Path)
    ap.add_argument("--por-pdf", type=int, default=4, help="Páginas a sacar de cada PDF")
    ap.add_argument("--ppp", type=int, default=300, help="Resolución cuando hay que dibujar la página")
    args = ap.parse_args()
    args.destino.mkdir(parents=True, exist_ok=True)
    pdfs = [args.origen] if args.origen.is_file() else sorted(args.origen.glob("*.pdf"))
    total = 0
    for pdf in pdfs:
        print(pdf.name)
        try:
            total += extraer(pdf, args.destino, args.por_pdf, args.ppp)
        except Exception as e:  # un PDF dañado no debe parar el resto
            print(f"  ERROR: {type(e).__name__}: {e}")
    print(f"{total} páginas en {args.destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
