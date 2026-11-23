"""Genera los pantallazos del manual con la aplicación en marcha (http://127.0.0.1:8765).

Uso:  python herramientas/capturas_manual.py docs/manual
Necesita Playwright (``pip install playwright``) y usa el Microsoft Edge ya instalado.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765"
destino = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/manual")
destino.mkdir(parents=True, exist_ok=True)


# Encaja el diagrama sin bajar de la escala a la que LiteGraph deja de dibujar títulos y controles
ENCUADRAR = """() => {
  const c = document.querySelector('#lienzo').getBoundingClientRect();
  let x1 = 1e9, y1 = 1e9, x2 = -1e9, y2 = -1e9;
  for (const n of grafo._nodes) { x1 = Math.min(x1, n.pos[0]); y1 = Math.min(y1, n.pos[1] - 30);
    x2 = Math.max(x2, n.pos[0] + n.size[0]); y2 = Math.max(y2, n.pos[1] + n.size[1]); }
  const e = Math.max(0.62, Math.min(1, (c.width - 40) / (x2 - x1), (c.height - 90) / (y2 - y1)));
  lienzo.ds.scale = e; lienzo.ds.offset[0] = 20 / e - x1; lienzo.ds.offset[1] = 20 / e - y1;
  lienzo.setDirty(true, true);
}"""
CERRAR_MENU = "() => document.querySelectorAll('.litecontextmenu').forEach((m) => m.remove())"


def esperar(page, ms=1800):
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)


def abrir(page, flujo, pagina):
    page.select_option("#flujos", label=flujo)
    esperar(page, 600)
    page.select_option("#pagina", pagina)
    esperar(page)
    page.evaluate(ENCUADRAR)
    esperar(page, 500)


def elegir(page, tipo, salida=None):
    page.evaluate("(t) => { const n = grafo._nodes.find((x) => x.type === t); lienzo.selectNode(n); seleccionar(n.id); }", tipo)
    esperar(page, 900)
    if salida:
        page.click(f'#visor-salidas button[data-salida="{salida}"]')
        esperar(page, 900)
    page.evaluate("ajustarVisor && ajustarVisor()")
    page.wait_for_timeout(400)


def foto(page, nombre, **kw):
    page.screenshot(path=str(destino / nombre), **kw)
    print("  ", nombre)


with sync_playwright() as p:
    nav = p.chromium.launch(channel="msedge", headless=True)
    ctx = nav.new_context(viewport={"width": 1920, "height": 1060}, device_scale_factor=1.25, locale="es-ES")
    page = ctx.new_page()
    page.goto(URL)
    page.evaluate("localStorage.setItem('lp.visor', '640')")
    page.reload()
    esperar(page, 2500)
    print("flujos:", page.eval_on_selector_all("#flujos option", "o => o.map(x => x.textContent)"))

    abrir(page, "1 Limpieza general", "CL-12-1909_p0007.png")
    elegir(page, "salida/guardar")
    foto(page, "01_pantalla_general.png")
    foto(page, "02_barra_superior.png", clip={"x": 0, "y": 0, "width": 1920, "height": 46})
    caja = page.locator("#paleta").bounding_box()
    foto(page, "03_paleta.png", clip=caja)
    caja = page.locator("#visor").bounding_box()
    foto(page, "04_visor.png", clip=caja)

    elegir(page, "entrada/pagina")
    foto(page, "05_nodo_entrada.png")
    elegir(page, "geometria/marco", "marco")
    foto(page, "06_marco_mascara.png")
    elegir(page, "geometria/marco")
    foto(page, "07_marco_resultado.png")
    caja = page.locator("#visor-info").bounding_box()
    if caja and caja["height"] > 10:
        foto(page, "08_parametros_marco.png", clip=caja)

    # menú del botón derecho sobre un nodo
    pos = page.evaluate("""() => { const n = grafo._nodes.find((x) => x.type === 'fondo/transparencia');
        const r = document.querySelector('#lienzo').getBoundingClientRect(); const d = lienzo.ds;
        return {x: r.left + (n.pos[0] + d.offset[0]) * d.scale + 60 * d.scale, y: r.top + (n.pos[1] + d.offset[1]) * d.scale - 10 * d.scale}; }""")
    page.mouse.click(pos["x"], pos["y"], button="right")
    page.wait_for_timeout(600)
    foto(page, "09_menu_nodo.png")
    page.evaluate(CERRAR_MENU)
    page.wait_for_timeout(300)

    abrir(page, "1 Limpieza general", "CL-1911_p0029.png")
    elegir(page, "salida/guardar")
    foto(page, "10_limpieza_texto.png")
    elegir(page, "ruido/motas", "quitado")
    foto(page, "11_motas_quitado.png")

    abrir(page, "2 Blanco y negro (Sauvola)", "CL-1911_p0029.png")
    elegir(page, "salida/guardar")
    foto(page, "12_blanco_y_negro.png")

    abrir(page, "3 Detectar texto", "ESC-CAB-1845_p0013.png")
    elegir(page, "neuronal/detector_texto", "vista")
    foto(page, "13_detectar_texto.png")
    abrir(page, "3 Detectar texto", "CL-1909-AP5_p0012.png")
    elegir(page, "texto/hay_texto", "vista")
    foto(page, "14_pagina_sin_texto.png")

    abrir(page, "1 Limpieza general", "CL-12-1909_p0007.png")
    page.click("#btn-lote")
    page.wait_for_timeout(700)
    foto(page, "15_procesar_carpeta.png")
    nav.close()
