"""Pruebas de LimpiaPáginas. Se ejecutan con ``pytest`` desde la raíz del proyecto."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from limpiapaginas import dispositivo, motor, nodos  # noqa: F401
from limpiapaginas.config import RAIZ
from limpiapaginas.lote import procesar_carpeta
from limpiapaginas.registro import IMAGEN, REGISTRO
from limpiapaginas.util import escribir_imagen

EJEMPLO = RAIZ / "datos" / "entrada" / "ejemplo_1.png"


@pytest.fixture
def pagina(tmp_path):
    """Página sintética: renglones de texto oscuro sobre papel gris con degradado y motas."""
    rng = np.random.default_rng(1)
    alto, ancho = 900, 700
    yy, xx = np.mgrid[0:alto, 0:ancho]
    img = (225 - 30 * xx / ancho + rng.normal(0, 4, (alto, ancho))).astype(np.float32)
    lienzo = np.full((alto, ancho), 255, np.uint8)
    for i, y in enumerate(range(90, 800, 42)):
        cv2.putText(lienzo, "Texto de prueba con tildes y cifras 1871", (50, y), cv2.FONT_HERSHEY_COMPLEX, 0.8, 0, 2)
    img = np.where(lienzo < 128, 35.0, img)
    for _ in range(60):                                   # motas aisladas en el margen inferior
        x, y = rng.integers(40, ancho - 40), rng.integers(830, alto - 20)
        img[y:y + 2, x:x + 2] = 30
    img = np.clip(img, 0, 255).astype(np.uint8)
    m = cv2.getRotationMatrix2D((ancho / 2, alto / 2), 1.5, 1.0)
    img = cv2.warpAffine(img, m, (ancho, alto), borderValue=200)
    ruta = tmp_path / "pagina.png"
    cv2.imwrite(str(ruta), img)
    return ruta


def _uno(tipo, ruta, **params):
    f = motor.cadena(("entrada/pagina", {"modo": "grises"}), (tipo, params))
    r = motor.ejecutar(f, ruta, cache=None)[2]
    assert r.estado == "ok", r.error
    return r


def test_registro_tiene_todas_las_categorias():
    categorias = {n.categoria for n in REGISTRO.values()}
    assert {"entrada", "salida", "color", "fondo", "ruido", "binarizar", "geometria", "texto", "mascara",
            "neuronal"} <= categorias
    assert not nodos.ERRORES_CARGA
    assert "mios/quitar_color" in REGISTRO          # el ejemplo de nodos_extra se carga solo


@pytest.mark.parametrize("tipo", sorted(t for t, s in REGISTRO.items() if list(s.entradas) == ["imagen"]
                                        and t != "neuronal/onnx"))
def test_cada_nodo_funciona_con_valores_por_defecto(tipo, pagina):
    r = _uno(tipo, pagina)
    for nombre, t in REGISTRO[tipo].salidas.items():
        v = r.salidas[nombre]
        if t == IMAGEN:
            assert isinstance(v, np.ndarray) and v.dtype == np.uint8 and v.ndim in (2, 3) and v.size > 0
        else:
            json.dumps(v)                              # los datos deben poder guardarse


@pytest.mark.parametrize("tipo", sorted(t for t, s in REGISTRO.items() if list(s.entradas) == ["imagen"]
                                        and t not in ("neuronal/onnx",)))
def test_cada_nodo_acepta_color(tipo, pagina, tmp_path):
    color = cv2.cvtColor(cv2.imread(str(pagina), 0), cv2.COLOR_GRAY2BGR)
    color[:, :, 2] = np.minimum(255, color[:, :, 2].astype(int) + 15)      # papel algo amarillento
    ruta = tmp_path / "color.png"
    cv2.imwrite(str(ruta), color)
    f = motor.cadena(("entrada/pagina", {"modo": "color"}), tipo)
    r = motor.ejecutar(f, ruta, cache=None)[2]
    assert r.estado == "ok", r.error


def test_enderezar_mide_el_angulo(pagina):
    r = _uno("geometria/enderezar", pagina)
    assert abs(r.salidas["datos"]["angulo"] + 1.5) < 0.25


def test_aplanar_deja_el_papel_blanco_y_uniforme(pagina):
    antes = cv2.imread(str(pagina), 0)
    despues = _uno("fondo/aplanar", pagina).salidas["imagen"]
    zona = (slice(820, 880), slice(60, 640))                # margen sin texto
    assert np.median(despues[zona]) > 245
    assert abs(float(despues[zona][:, :100].mean()) - float(despues[zona][:, -100:].mean())) < 3
    assert abs(float(antes[zona][:, :100].mean()) - float(antes[zona][:, -100:].mean())) > 10
    assert despues[cv2.imread(str(pagina), 0) < 60].mean() < 90   # la tinta sigue oscura


def test_motas_borra_lo_aislado_y_respeta_la_puntuacion():
    img = np.full((300, 600), 255, np.uint8)
    cv2.putText(img, "caf\xe9 i.j; a\xf1o", (40, 120), cv2.FONT_HERSHEY_COMPLEX, 1.6, 0, 3)
    arriba = int(np.flatnonzero((img[:, 80:110] < 128).any(axis=1))[0])   # borde superior de las letras
    y = arriba - 8
    cv2.circle(img, (95, y), 2, 0, -1)                     # «tilde»: punto pequeño justo encima del texto
    img[250:252, 500:502] = 0                              # mota lejos del texto
    spec = REGISTRO["ruido/motas"]
    salida, quitado = spec.funcion(motor.Contexto(), img, **spec.normalizar_params({"area_max": 20}))
    assert salida[250:252, 500:502].min() == 255            # la mota aislada desaparece
    assert salida[y - 2:y + 3, 93:98].min() == 0            # el punto junto a las letras se conserva
    assert quitado[250, 500] == 255 and quitado[y, 95] == 0
    sin_proteger, _ = spec.funcion(motor.Contexto(), img, **spec.normalizar_params({"area_max": 20, "proteccion": 0}))
    assert sin_proteger[y - 2:y + 3, 93:98].min() == 255    # sin protección, también se lo lleva


def _pagina_con_marco():
    img = np.full((1400, 1000), 235, np.uint8)
    for i in range(12):
        cv2.putText(img, "Texto de prueba del renglon", (150, 300 + 70 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 20, 3)
    return img


def test_marco_borra_las_lineas_del_borde_y_respeta_el_texto():
    img = _pagina_con_marco()
    antes = img.copy()
    cv2.line(img, (60, 50), (960, 50), 0, 3)          # canto superior de la hoja
    cv2.line(img, (960, 50), (960, 1360), 0, 4)       # canto derecho
    cv2.line(img, (60, 1360), (960, 1360), 0, 3)      # canto inferior
    spec = REGISTRO["geometria/marco"]
    salida, mascara, datos = spec.funcion(motor.Contexto(), img, **spec.normalizar_params({}))
    assert datos["lineas"] == 3
    assert salida[45:56, 100:900].min() > 200 and salida[100:1300, 955:966].min() > 200
    assert np.array_equal(salida[200:1200, 100:900], antes[200:1200, 100:900])      # el texto, intacto


def test_marco_respeta_tablas_y_rayas_de_cabecera():
    img = _pagina_con_marco()
    cv2.putText(img, "15", (470, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 20, 3)        # número de página
    cv2.line(img, (60, 120), (940, 120), 0, 3)        # raya de cabecera: tiene el número encima
    cv2.rectangle(img, (60, 180), (940, 1200), 0, 3)  # tabla: recuadro con filetes interiores
    for x in (300, 520, 740):
        cv2.line(img, (x, 180), (x, 1200), 0, 2)
    spec = REGISTRO["geometria/marco"]
    salida, mascara, datos = spec.funcion(motor.Contexto(), img, **spec.normalizar_params({}))
    assert datos["lineas"] == 0 and np.array_equal(salida, img)


def test_transparencia_no_borra_una_pagina_en_blanco_y_negro_puro():
    # Escaneo de 1 bit (solo 0 y 255): no hay nada transparentado y la tinta debe quedar intacta
    img = np.full((200, 300), 255, np.uint8)
    cv2.putText(img, "Texto", (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 2, 0, 4)
    _, img = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY)
    spec = REGISTRO["fondo/transparencia"]
    salida, tinta = spec.funcion(motor.Contexto(), img, **spec.normalizar_params({}))
    assert np.array_equal(salida, img)
    assert (tinta > 0).sum() == (img == 0).sum()


def test_transparencia_borra_el_trazo_claro_y_conserva_la_tinta():
    img = np.full((200, 600), 225, np.uint8)
    cv2.putText(img, "anverso", (30, 80), cv2.FONT_HERSHEY_COMPLEX, 1.5, 30, 3)
    cv2.putText(img, "reverso", (300, 160), cv2.FONT_HERSHEY_COMPLEX, 1.5, 170, 3)
    spec = REGISTRO["fondo/transparencia"]
    salida, tinta = spec.funcion(motor.Contexto(), img, **spec.normalizar_params({}))
    assert salida[110:190, 290:590].min() == 255            # el texto transparentado desaparece
    assert (salida[30:90, 30:280] < 60).sum() > 500         # el del anverso sigue
    assert tinta[110:190, 290:590].max() == 0


def test_detectores_distinguen_pagina_con_y_sin_texto(pagina, tmp_path):
    for tipo in ("texto/hay_texto", "neuronal/detector_texto"):
        assert _uno(tipo, pagina).salidas["datos"]["hay_texto"] is True
    rng = np.random.default_rng(0)
    vacia = np.clip(rng.normal(220, 5, (900, 700)), 0, 255).astype(np.uint8)
    cv2.circle(vacia, (350, 450), 200, 60, 6)               # un dibujo, no texto
    ruta = tmp_path / "vacia.png"
    cv2.imwrite(str(ruta), vacia)
    for tipo in ("texto/hay_texto", "neuronal/detector_texto"):
        assert _uno(tipo, ruta).salidas["datos"]["hay_texto"] is False


def test_binarizacion_devuelve_solo_blanco_y_negro(pagina):
    for tipo, params in [("binarizar/otsu", {}), ("binarizar/adaptativa", {}), ("binarizar/umbral", {}),
                         ("binarizar/local", {"metodo": "sauvola"}), ("binarizar/local", {"metodo": "wolf"}),
                         ("binarizar/local", {"metodo": "nick", "k": -0.15}),
                         ("binarizar/local", {"metodo": "niblack", "k": -0.2})]:
        b = _uno(tipo, pagina, **params).salidas["imagen"]
        assert set(np.unique(b)) <= {0, 255}, tipo
        assert 0.01 < (b == 0).mean() < 0.6, (tipo, params)   # tinta negra sobre blanco, no al revés


# ---------------------------------------------------------------- rutas de GPU (probadas en CPU con PyTorch)

@pytest.fixture
def gpu_simulada(monkeypatch):
    torch = pytest.importorskip("torch")
    monkeypatch.setattr(dispositivo, "usar_gpu", lambda: True)
    monkeypatch.setattr(dispositivo, "torch_device", lambda: torch.device("cpu"))


@pytest.mark.parametrize("tipo,params,tolerancia", [
    ("ruido/gaussiano", {"tam": 7}, 1), ("ruido/gaussiano", {"tam": 9, "sigma": 2.5}, 1),
    ("fondo/aplanar", {}, 6), ("fondo/aplanar", {"modo": "restar"}, 6),
])
def test_version_gpu_equivale_a_cpu(tipo, params, tolerancia, pagina, gpu_simulada, monkeypatch):
    en_gpu = _uno(tipo, pagina, **params)
    assert en_gpu.gpu and not en_gpu.avisos
    monkeypatch.setattr(dispositivo, "usar_gpu", lambda: False)
    en_cpu = _uno(tipo, pagina, **params)
    assert not en_cpu.gpu
    dif = np.abs(en_gpu.salidas["imagen"].astype(int) - en_cpu.salidas["imagen"].astype(int))
    assert np.percentile(dif, 99) <= tolerancia, f"diferencia p99 = {np.percentile(dif, 99)}"


def test_sauvola_gpu_equivale_a_cpu(pagina, gpu_simulada, monkeypatch):
    en_gpu = _uno("binarizar/local", pagina)
    monkeypatch.setattr(dispositivo, "usar_gpu", lambda: False)
    en_cpu = _uno("binarizar/local", pagina)
    assert en_gpu.gpu and (en_gpu.salidas["imagen"] != en_cpu.salidas["imagen"]).mean() < 0.002


def test_si_la_gpu_falla_se_usa_la_cpu_y_se_avisa(pagina, monkeypatch):
    torch = pytest.importorskip("torch")
    if torch.cuda.is_available():
        pytest.skip("hay GPU real")
    monkeypatch.setattr(dispositivo, "usar_gpu", lambda: True)       # torch_device() pedirá «cuda» y fallará
    monkeypatch.setattr(dispositivo, "torch_device", lambda: torch.device("cuda"))
    r = _uno("ruido/gaussiano", pagina)
    assert not r.gpu and r.avisos and "CPU" in r.avisos[0]


# ---------------------------------------------------------------- motor

def test_cache_solo_recalcula_desde_el_nodo_cambiado(pagina):
    cache = motor.Cache(64)
    f = motor.cadena(("entrada/pagina", {"modo": "grises"}), "fondo/aplanar", "ruido/gaussiano", "binarizar/otsu")
    r1 = motor.ejecutar(f, pagina, cache=cache)
    assert not any(r.de_cache for r in r1.values())
    f["nodos"][2]["params"]["tam"] = 9
    r2 = motor.ejecutar(f, pagina, cache=cache)
    assert [r2[i].de_cache for i in (1, 2, 3, 4)] == [True, True, False, False]
    assert not np.array_equal(r1[3].salidas["imagen"], r2[3].salidas["imagen"])


def test_nodo_desactivado_deja_pasar_la_imagen(pagina):
    f = motor.cadena(("entrada/pagina", {"modo": "grises"}), "color/invertir", "ruido/gaussiano")
    f["nodos"][1]["activo"] = False
    r = motor.ejecutar(f, pagina, cache=None)
    assert r[2].estado == "desactivado"
    assert np.array_equal(r[2].salidas["imagen"], r[1].salidas["imagen"])


def test_errores_del_flujo(pagina):
    f = motor.cadena(("entrada/pagina", {}), "color/invertir")
    f["enlaces"].append([2, "imagen", 1, "imagen"])              # enlace a una entrada que no existe: se ignora
    assert motor.ejecutar(f, pagina, cache=None)[2].estado == "ok"
    ciclo = {"nodos": [{"id": 1, "tipo": "color/invertir"}, {"id": 2, "tipo": "color/invertir"}],
             "enlaces": [[1, "imagen", 2, "imagen"], [2, "imagen", 1, "imagen"]]}
    with pytest.raises(motor.ErrorFlujo):
        motor.ejecutar(ciclo, pagina)
    with pytest.raises(motor.ErrorFlujo):
        motor.ejecutar({"nodos": [{"id": 1, "tipo": "no/existe"}], "enlaces": []}, pagina)
    suelto = {"nodos": [{"id": 1, "tipo": "color/invertir"}, {"id": 2, "tipo": "ruido/mediana"}],
              "enlaces": [[1, "imagen", 2, "imagen"]]}
    r = motor.ejecutar(suelto, pagina, cache=None)
    assert r[1].estado == "omitido" and "imagen" in r[1].error and r[2].estado == "omitido"


def test_un_nodo_que_falla_no_tumba_el_resto(pagina):
    f = motor.cadena(("entrada/pagina", {}), "color/invertir")
    f["nodos"].append({"id": 3, "tipo": "entrada/archivo", "params": {"ruta": "no_existe.png"}})
    r = motor.ejecutar(f, pagina, cache=None)
    assert r[3].estado == "error" and r[2].estado == "ok"


def test_parametros_fuera_de_rango_se_corrigen():
    spec = REGISTRO["ruido/gaussiano"]
    assert spec.normalizar_params({"tam": 8})["tam"] == 9            # debe ser impar
    assert spec.normalizar_params({"tam": 9999})["tam"] == 99
    assert spec.normalizar_params({"tam": "abc", "sigma": -3})["sigma"] == 0.0
    assert REGISTRO["binarizar/local"].normalizar_params({"metodo": "inventado"})["metodo"] == "sauvola"


def test_flujos_de_partida_son_validos_y_funcionan():
    flujos = sorted((RAIZ / "flujos").glob("[0-9]*.json"))
    assert len(flujos) >= 3
    for ruta in flujos:
        f = motor.cargar_flujo(ruta)
        r = motor.ejecutar(f, EJEMPLO, cache=None)
        assert all(x.estado == "ok" for x in r.values()), (ruta.name, [x.error for x in r.values() if x.error])


# ---------------------------------------------------------------- lote y modelo ONNX

def test_lote_escribe_paginas_e_informe(tmp_path, pagina):
    entrada, salida = tmp_path / "in", tmp_path / "out"
    (entrada / "libro1").mkdir(parents=True)
    img = cv2.imread(str(pagina), 0)
    cv2.imwrite(str(entrada / "a.png"), img)
    # cv2.imwrite no admite tildes en la ruta en Windows; se usa la escritura propia del proyecto
    escribir_imagen(entrada / "libro1" / "p\xe1gina 2.jpg", img)   # subcarpeta, tilde y espacio
    (entrada / "notas.txt").write_text("no es una imagen")
    f = motor.cargar_flujo(RAIZ / "flujos" / "1 Limpieza general.json")
    p = procesar_carpeta(f, entrada, salida, recursivo=True)
    assert (p.hechas, p.errores) == (2, 0)
    assert (salida / "a.png").exists() and (salida / "libro1" / "p\xe1gina 2.png").exists()
    informe = (salida / "informe.csv").read_text(encoding="utf-8-sig")
    assert "hay_texto" in informe and "Enderezar.angulo" in informe and informe.count("\n") == 3
    assert (salida / "flujo_usado.json").exists()
    limpia = cv2.imread(str(salida / "a.png"), 0)
    assert np.median(limpia) == 255


def test_lote_sin_nodo_de_salida_da_error_claro(tmp_path, pagina):
    f = motor.cadena(("entrada/pagina", {}), "color/invertir")
    with pytest.raises(motor.ErrorFlujo, match="Guardar"):
        procesar_carpeta(f, pagina.parent, tmp_path / "out")


def test_modelo_onnx_generico_con_teselas(tmp_path, pagina):
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper
    # Red mínima: salida = 1 - entrada (un «invertir» en el rango 0 a 1)
    uno = helper.make_tensor("uno", TensorProto.FLOAT, [1], [1.0])
    g = helper.make_graph([helper.make_node("Sub", ["uno", "x"], ["y"])], "inv",
                          [helper.make_tensor_value_info("x", TensorProto.FLOAT, ["n", 1, "h", "w"])],
                          [helper.make_tensor_value_info("y", TensorProto.FLOAT, ["n", 1, "h", "w"])], [uno])
    modelo = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)])
    modelo.ir_version = 8
    ruta = tmp_path / "invertir.onnx"
    onnx.save(modelo, str(ruta))
    r = _uno("neuronal/onnx", pagina, modelo=str(ruta), tesela=256, solape=16)
    original = cv2.imread(str(pagina), 0)
    assert r.salidas["imagen"].shape == original.shape
    assert np.abs(r.salidas["imagen"].astype(int) - (255 - original.astype(int))).max() <= 1   # sin juntas


# ---------------------------------------------------------------- API web

@pytest.fixture
def cliente():
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from limpiapaginas import servidor
    return TestClient(servidor.app)


def test_api_basica(cliente):
    assert cliente.get("/").status_code == 200
    nodos_api = cliente.get("/api/nodos").json()
    assert len(nodos_api["nodos"]) == len(REGISTRO)
    assert "ejemplo_1.png" in cliente.get("/api/paginas").json()["paginas"]
    f = motor.cargar_flujo(RAIZ / "flujos" / "1 Limpieza general.json")
    r = cliente.post("/api/ejecutar", json={"flujo": f, "pagina": "ejemplo_1.png"}).json()
    assert all(n["estado"] == "ok" for n in r["nodos"].values())
    img = cliente.get("/api/salida/7/imagen?max=200")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    completa = cliente.get("/api/salida/7/imagen")
    assert cv2.imdecode(np.frombuffer(completa.content, np.uint8), 0).shape == (2000, 1400)
    assert cliente.get("/api/salida/99/imagen").status_code == 404


def test_api_rechaza_rutas_fuera_de_la_carpeta(cliente):
    assert cliente.get("/api/pagina/..%2F..%2Fconfig.toml").status_code in (400, 404)
    r = cliente.post("/api/ejecutar", json={"flujo": {"nodos": [], "enlaces": []}, "pagina": "../config.toml"})
    assert r.status_code == 400
    ciclo = {"nodos": [{"id": 1, "tipo": "no/existe"}], "enlaces": []}
    assert cliente.post("/api/ejecutar", json={"flujo": ciclo, "pagina": "ejemplo_1.png"}).status_code == 422


def test_api_no_deja_escribir_el_lote_sobre_los_originales(cliente):
    f = motor.cargar_flujo(RAIZ / "flujos" / "1 Limpieza general.json")
    carpeta = str(RAIZ / "datos" / "entrada")
    r = cliente.post("/api/lote", json={"flujo": f, "entrada": carpeta, "salida": carpeta})
    assert r.status_code == 400 and "originales" in r.json()["detail"]


# ------------------------------------------------------------------ revisión del 23-11-2026

def _nodo(tipo, img, **params):
    spec = REGISTRO[tipo]
    ctx = motor.Contexto()
    return spec.funcion(ctx, img, **spec.normalizar_params(params)), ctx


def test_marco_conserva_un_numero_de_pagina_suelto_sobre_la_raya():
    img = np.full((3900, 2550), 255, np.uint8)
    cv2.line(img, (100, 520), (2400, 520), 0, 4)
    cv2.putText(img, "7", (1200, 500), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 0, 2)
    antes = int((img[470:505, 1190:1230] < 128).sum())
    (salida, _, datos), _ = _nodo("geometria/marco", img)
    assert antes > 50
    assert int((salida[470:505, 1190:1230] < 128).sum()) == antes       # la cifra sigue entera


def test_bordes_no_borra_una_pagina_de_papel_oscuro_ni_en_negativo():
    oscura = np.full((1200, 900), 80, np.uint8)
    cv2.putText(oscura, "Texto que no debe desaparecer", (60, 600), cv2.FONT_HERSHEY_SIMPLEX, 1.5, 20, 3)
    (salida, _), _ = _nodo("geometria/bordes", oscura)
    assert np.array_equal(salida, oscura)
    negativo = np.zeros((1200, 900), np.uint8)
    cv2.putText(negativo, "Texto en negativo", (60, 600), cv2.FONT_HERSHEY_SIMPLEX, 1.5, 255, 3)
    (salida, _), _ = _nodo("geometria/bordes", negativo)
    assert np.array_equal(salida, negativo)


def test_bordes_sigue_quitando_la_banda_negra_del_lomo():
    img = np.full((1200, 900), 225, np.uint8)
    img[:, :40] = 10
    cv2.putText(img, "Texto", (300, 600), cv2.FONT_HERSHEY_SIMPLEX, 1.5, 20, 3)
    (salida, _), _ = _nodo("geometria/bordes", img)
    assert salida[:, :40].min() > 200 and (salida[500:650, 280:500] < 60).any()


def test_enderezar_no_gira_una_pagina_en_blanco_ni_una_diminuta():
    for img in (np.full((600, 800), 255, np.uint8), np.full((200, 20), 255, np.uint8)):
        (_, datos), _ = _nodo("geometria/enderezar", img)
        assert datos["angulo"] == 0 and not datos["girada"]


def test_motas_no_borra_una_pagina_de_letra_diminuta():
    img = np.full((300, 400), 255, np.uint8)
    for y in range(40, 280, 12):
        for x in range(20, 380, 6):
            img[y:y + 3, x:x + 2] = 0           # «letras» de 6 píxeles: todas por debajo del área máxima
    (salida, quitado), ctx = _nodo("ruido/motas", img)
    assert np.array_equal(salida, img) and not quitado.any() and ctx.avisos


def test_lee_bien_un_escaneo_de_12_bits_guardado_en_16(tmp_path):
    from limpiapaginas.util import leer_imagen
    base = np.full((60, 80), 230, np.uint8)
    base[20:40, 20:60] = 30
    for nombre, factor in (("doce.png", 16), ("dieciseis.png", 257)):
        ruta = tmp_path / nombre
        cv2.imwrite(str(ruta), base.astype(np.uint16) * factor)
        for modo in ("original", "grises", "color"):
            leida = leer_imagen(ruta, modo)
            assert leida.dtype == np.uint8 and abs(int(leida.max()) - 230) <= 1 and abs(int(leida.min()) - 30) <= 1


def test_redimensionar_nunca_deja_una_imagen_vacia():
    (salida), _ = _nodo("geometria/redimensionar", np.full((1, 1), 255, np.uint8), factor=0.1)
    assert salida.shape == (1, 1)


def test_lote_no_escribe_encima_de_los_originales(tmp_path, pagina):
    flujo = motor.cadena(("entrada/pagina", {}), ("salida/guardar", {"formato": "png"}))
    original = pagina.read_bytes()
    with pytest.raises(motor.ErrorFlujo):
        procesar_carpeta(flujo, pagina.parent, pagina.parent)
    assert pagina.read_bytes() == original


def test_lote_no_reprocesa_una_salida_que_esta_dentro_de_la_entrada(tmp_path, pagina):
    flujo = motor.cadena(("entrada/pagina", {}), ("salida/guardar", {"formato": "png"}))
    salida = pagina.parent / "limpias"
    for _ in range(2):
        p = procesar_carpeta(flujo, pagina.parent, salida, recursivo=True)
        assert p.total == 1 and p.errores == 0
    assert not (salida / "limpias").exists()


def test_lote_no_pisa_dos_paginas_con_el_mismo_nombre_y_distinta_extension(tmp_path, pagina):
    entrada = tmp_path / "e"
    entrada.mkdir()
    img = cv2.imread(str(pagina), 0)
    cv2.imwrite(str(entrada / "a.png"), img)
    cv2.imwrite(str(entrada / "a.jpg"), img)
    flujo = motor.cadena(("entrada/pagina", {}), ("salida/guardar", {"formato": "png"}))
    p = procesar_carpeta(flujo, entrada, tmp_path / "s")
    assert p.errores == 0
    assert sorted(f.name for f in (tmp_path / "s").glob("*.png")) == ["a_jpg.png", "a_png.png"]


def test_guardar_rechaza_un_sufijo_que_saldria_de_la_carpeta(tmp_path, pagina):
    flujo = motor.cadena(("entrada/pagina", {}), ("salida/guardar", {"formato": "png", "sufijo": "/../../x"}))
    p = procesar_carpeta(flujo, pagina.parent, tmp_path / "s" / "dentro")
    assert p.errores == 1 and not list(tmp_path.glob("x*")) and not list((tmp_path / "s").glob("x*"))


def test_api_subir_no_pisa_una_pagina_existente(cliente, tmp_path, monkeypatch):
    from limpiapaginas import servidor
    monkeypatch.setattr(servidor.S, "carpeta_entrada", tmp_path)
    (tmp_path / "hoja.png").write_bytes(b"original")
    r = cliente.post("/api/subir", files=[("ficheros", ("hoja.png", b"nueva", "image/png"))])
    assert r.status_code == 200 and r.json()["guardados"] == ["hoja (2).png"]
    assert (tmp_path / "hoja.png").read_bytes() == b"original"
