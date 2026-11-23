/* LimpiaPáginas — interfaz.
 *
 * El diagrama se dibuja con LiteGraph. Los tipos de nodo NO están escritos aquí:
 * se piden al servidor (/api/nodos), que los saca de las funciones de Python
 * decoradas con @nodo. Añadir un proceso nuevo no requiere tocar este fichero.
 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const ALTO_MINI = 150;           // alto reservado en cada nodo para su miniatura
const COLOR_TIPO = { imagen: "#6bb6ff", datos: "#e3b341" };

const E = {                      // estado de la aplicación
  specs: {},                     // tipo -> descripción del nodo
  categorias: {},
  resultados: {},                // id de nodo -> resultado de la última ejecución
  pagina: null,
  paginas: [],
  flujoNombre: "",
  ejecutando: false,
  pendiente: false,
  cargando: false,               // true mientras se reconstruye el diagrama: no auto-ejecutar
  visor: { nodo: null, salida: null, img: null, original: null, verOriginal: false,
           zoom: 1, x: 0, y: 0, ajustado: true, clave: "" },
};

// ------------------------------------------------------------------ utilidades

async function api(ruta, opciones = {}) {
  if (opciones.json !== undefined) {
    opciones.method = opciones.method || "POST";
    opciones.headers = { "Content-Type": "application/json" };
    opciones.body = JSON.stringify(opciones.json);
  }
  const r = await fetch(ruta, opciones);
  if (!r.ok) {
    let detalle = r.statusText;
    try { detalle = (await r.json()).detail || detalle; } catch (_) { /* sin cuerpo JSON */ }
    throw new Error(typeof detalle === "string" ? detalle : JSON.stringify(detalle));
  }
  return r.json();
}

function avisar(texto, clase = "", ms = 4500) {
  const d = document.createElement("div");
  d.className = "aviso " + clase;
  d.textContent = texto;
  $("#avisos").append(d);
  setTimeout(() => d.remove(), ms);
}

function pedirTexto(titulo, ayuda, valor) {
  return new Promise((resolver) => {
    const dlg = $("#dlg-texto");
    $("#dlg-texto-titulo").textContent = titulo;
    $("#dlg-texto-ayuda").textContent = ayuda || "";
    $("#dlg-texto-valor").value = valor || "";
    dlg.returnValue = "";
    $("#dlg-texto-cancelar").onclick = () => dlg.close("cancelar");
    dlg.onclose = () => resolver(dlg.returnValue === "cancelar" ? null : $("#dlg-texto-valor").value.trim());
    dlg.showModal();
    $("#dlg-texto-valor").select();
  });
}

const escapar = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ------------------------------------------------------------------ diagrama (LiteGraph)

LiteGraph.release_link_on_empty_shows_menu = true;
LiteGraph.alt_drag_do_clone_nodes = true;
Object.assign(LGraphCanvas.link_type_colors, COLOR_TIPO);

const grafo = new LGraph();
const lienzo = new LGraphCanvas("#lienzo", grafo);
lienzo.render_canvas_border = false;
lienzo.show_info = false;
lienzo.render_connection_arrows = false;
lienzo.connections_width = 3;
lienzo.default_link_color = "#8a8f9a";
Object.assign(lienzo.default_connection_color_byType, COLOR_TIPO);
Object.assign(lienzo.default_connection_color_byTypeOff, COLOR_TIPO);
lienzo.onShowNodePanel = () => {};               // el doble clic no abre el panel de propiedades en inglés

function ajustarLienzo() {
  const z = $("#zona-lienzo");
  lienzo.resize(z.clientWidth, z.clientHeight);
  dibujarVisor();
}
window.addEventListener("resize", ajustarLienzo);

function decimales(paso) {
  const s = String(paso);
  return s.includes(".") ? s.split(".")[1].length : 0;
}

function normalizar(p, v) {
  if (p.tipo === "entero") {
    v = Math.round(Number(v));
    if (!Number.isFinite(v)) v = p.defecto;
    v = Math.min(p.max, Math.max(p.min, v));
    if (p.impar && v % 2 === 0) v = v + 1 <= p.max ? v + 1 : v - 1;
  } else if (p.tipo === "decimal") {
    v = Number(v);
    if (!Number.isFinite(v)) v = p.defecto;
    v = Math.min(p.max, Math.max(p.min, v));
    v = Number(v.toFixed(Math.max(decimales(p.paso), 2)));
  }
  return v;
}

function crearControl(nodo, p) {
  const alCambiar = function (valor, _lienzo, n) {
    const v = normalizar(p, valor);
    n.properties[p.nombre] = v;
    this.value = v;                       // «this» es el control
    cambio();
  };
  let w;
  if (p.tipo === "entero") {
    w = nodo.addWidget("number", p.etiqueta, p.defecto, null,
      { min: p.min, max: p.max, step: p.impar ? 20 : 10, precision: 0, property: p.nombre });
  } else if (p.tipo === "decimal") {
    w = nodo.addWidget("number", p.etiqueta, p.defecto, null,
      { min: p.min, max: p.max, step: p.paso * 10, precision: Math.max(decimales(p.paso), 1), property: p.nombre });
  } else if (p.tipo === "opcion") {
    w = nodo.addWidget("combo", p.etiqueta, p.defecto, null, { values: p.valores, property: p.nombre });
  } else if (p.tipo === "logico") {
    w = nodo.addWidget("toggle", p.etiqueta, p.defecto, null, { on: "sí", off: "no", property: p.nombre });
  } else {
    w = nodo.addWidget("text", p.etiqueta, p.defecto, null, { property: p.nombre });
  }
  w.callback = alCambiar.bind(w);
  w.param = p;
}

function registrarNodos(specs) {
  for (const tipo of Object.keys(LiteGraph.registered_node_types)) LiteGraph.unregisterNodeType(tipo);
  E.specs = {};
  for (const spec of specs) {
    E.specs[spec.tipo] = spec;

    function Nodo() {
      for (const e of spec.entradas) this.addInput(e.nombre, e.tipo);
      for (const s of spec.salidas) this.addOutput(s.nombre, s.tipo);
      this.properties = {};
      for (const p of spec.params) { this.properties[p.nombre] = p.defecto; crearControl(this, p); }
      this.activo = true;
      this.size = this.computeSize();
    }
    Nodo.title = spec.titulo;
    Nodo.desc = spec.descripcion;

    // Alto del nodo sin la miniatura (ranuras + controles)
    Nodo.prototype.altoBase = function () {
      return LGraphNode.prototype.computeSize.call(this)[1];
    };
    Nodo.prototype.computeSize = function () {
      const t = LGraphNode.prototype.computeSize.call(this);
      t[0] = Math.max(t[0], 236);
      if (spec.salidas.length) t[1] += ALTO_MINI;
      return t;
    };

    Nodo.prototype.onDrawBackground = function (ctx) {
      if (this.flags.collapsed) return;
      const y0 = this.altoBase(), ancho = this.size[0] - 12, alto = this.size[1] - y0 - 22;
      const r = E.resultados[this.id];
      if (this._mini && this._mini.complete && this._mini.naturalWidth && alto > 20) {
        const k = Math.min(ancho / this._mini.naturalWidth, alto / this._mini.naturalHeight);
        const w = this._mini.naturalWidth * k, h = this._mini.naturalHeight * k;
        ctx.fillStyle = "#101114";
        ctx.fillRect(6, y0, ancho, alto);
        ctx.drawImage(this._mini, 6 + (ancho - w) / 2, y0 + (alto - h) / 2, w, h);
      }
      // Línea de estado al pie
      let texto = "", color = "#979daa";
      if (!this.activo) { texto = "desactivado: deja pasar la imagen"; }
      else if (!r) { texto = ""; }
      else if (r.estado === "error") { texto = "✖ " + r.error; color = "#e5645f"; }
      else if (r.estado === "omitido") { texto = "— " + r.error; color = "#d9a441"; }
      else {
        texto = (r.cache ? "en caché" : Math.round(r.ms) + " ms") + (r.gpu ? " · GPU" : "");
        const datos = Object.values(r.salidas).find((s) => s.tipo === "datos");
        if (datos) {
          const partes = Object.entries(datos.valor).filter(([, v]) => typeof v !== "object").slice(0, 2)
            .map(([k, v]) => `${k}: ${v === true ? "sí" : v === false ? "no" : v}`);
          if (partes.length) texto += " · " + partes.join(" · ");
        }
        if (r.avisos && r.avisos.length) color = "#d9a441";
      }
      if (texto) {
        ctx.font = "11px system-ui, sans-serif";
        ctx.fillStyle = color;
        ctx.textAlign = "left";
        let t = texto;
        while (t.length > 4 && ctx.measureText(t).width > this.size[0] - 14) t = t.slice(0, -2);
        ctx.fillText(t === texto ? t : t + "…", 7, this.size[1] - 7);
      }
    };

    Nodo.prototype.onConnectionsChange = function () { cambio(); };

    LiteGraph.registerNodeType(spec.tipo, Nodo);
  }
}

// Menú del botón derecho sobre un nodo (sustituye al de LiteGraph, que está en inglés)
function menuNodo(n) {
  const previo = vecino(n, -1), siguiente = vecino(n, +1);
  return [
    { content: n.activo ? "Desactivar (dejar pasar)" : "Activar", callback: () => { activar(n, !n.activo); } },
    null,
    { content: "⇄ Intercambiar con el anterior", disabled: !previo, callback: () => intercambiar(previo, n) },
    { content: "⇄ Intercambiar con el siguiente", disabled: !siguiente, callback: () => intercambiar(n, siguiente) },
    null,
    { content: "Duplicar", callback: () => duplicar(n) },
    { content: "Restaurar valores por defecto", callback: () => { ponerParams(n, {}); cambio(); } },
    null,
    { content: "Eliminar y unir sus vecinos", callback: () => eliminarUniendo(n) },
    { content: "Eliminar", callback: () => { grafo.remove(n); } },
  ];
}
grafo.onGetNodeMenuOptions = (opciones, n) => { opciones.splice(0, opciones.length, ...menuNodo(n)); };

function ponerParams(nodo, params) {
  const spec = E.specs[nodo.type];
  for (const p of spec.params) {
    const v = normalizar(p, params[p.nombre] !== undefined ? params[p.nombre] : p.defecto);
    nodo.properties[p.nombre] = v;
    const w = (nodo.widgets || []).find((x) => x.param && x.param.nombre === p.nombre);
    if (w) w.value = v;
  }
}

function activar(nodo, activo) {
  nodo.activo = activo;
  nodo.color = activo ? undefined : "#3a3a3a";
  nodo.bgcolor = activo ? undefined : "#2a2a2a";
  nodo.title = E.specs[nodo.type].titulo + (activo ? "" : "  ⏸");
  lienzo.setDirty(true, true);
  cambio();
}

// Primera ranura de imagen de un nodo (entrada o salida)
const ranuraImagen = (lista) => (lista || []).findIndex((r) => r.type === "imagen");

/** Nodo conectado a la entrada principal (-1) o a la salida principal (+1), si es una cadena simple. */
function vecino(nodo, sentido) {
  if (sentido < 0) {
    const i = ranuraImagen(nodo.inputs);
    if (i < 0 || nodo.inputs[i].link == null) return null;
    const enlace = grafo.links[nodo.inputs[i].link];
    const origen = enlace && grafo.getNodeById(enlace.origin_id);
    return origen && enlace.origin_slot === ranuraImagen(origen.outputs) ? origen : null;
  }
  const o = ranuraImagen(nodo.outputs);
  if (o < 0) return null;
  for (const id of nodo.outputs[o].links || []) {
    const enlace = grafo.links[id];
    const destino = enlace && grafo.getNodeById(enlace.target_id);
    if (destino && enlace.target_slot === ranuraImagen(destino.inputs) && ranuraImagen(destino.outputs) >= 0) return destino;
  }
  return null;
}

/** X → A → B → Y  pasa a ser  X → B → A → Y, y los dos nodos cambian de sitio. */
function intercambiar(a, b) {
  if (!a || !b) return;
  E.cargando = true;
  const ia = ranuraImagen(a.inputs), oa = ranuraImagen(a.outputs);
  const ib = ranuraImagen(b.inputs), ob = ranuraImagen(b.outputs);
  const entra = ia >= 0 && a.inputs[ia].link != null ? grafo.links[a.inputs[ia].link] : null;
  const origen = entra ? [grafo.getNodeById(entra.origin_id), entra.origin_slot] : null;
  const destinos = (b.outputs[ob].links || []).map((id) => grafo.links[id]).filter(Boolean)
    .map((l) => [grafo.getNodeById(l.target_id), l.target_slot]);
  if (ia >= 0) a.disconnectInput(ia);
  b.disconnectInput(ib);
  b.disconnectOutput(ob);
  if (origen && ib >= 0) origen[0].connect(origen[1], b, ib);
  if (ia >= 0) b.connect(ob, a, ia);
  for (const [nodo, ranura] of destinos) a.connect(oa, nodo, ranura);
  const pos = [a.pos[0], a.pos[1]];
  a.pos = [b.pos[0], b.pos[1]];
  b.pos = pos;
  E.cargando = false;
  lienzo.setDirty(true, true);
  cambio();
}

function eliminarUniendo(n) {
  const previo = vecino(n, -1);
  const o = ranuraImagen(n.outputs);
  const destinos = o >= 0 ? (n.outputs[o].links || []).map((id) => grafo.links[id]).filter(Boolean)
    .map((l) => [grafo.getNodeById(l.target_id), l.target_slot]) : [];
  E.cargando = true;
  grafo.remove(n);
  if (previo) for (const [nodo, ranura] of destinos) previo.connect(ranuraImagen(previo.outputs), nodo, ranura);
  E.cargando = false;
  cambio();
}

function duplicar(n) {
  const copia = LiteGraph.createNode(n.type);
  copia.pos = [n.pos[0] + 30, n.pos[1] + n.size[1] + 30];
  grafo.add(copia);
  ponerParams(copia, n.properties);
  lienzo.selectNode(copia);
}

/** Añade un nodo desde la paleta: detrás del seleccionado y, si este ya tenía salida, en medio. */
function anadirNodo(tipo) {
  const nuevo = LiteGraph.createNode(tipo);
  if (!nuevo) return;
  const sel = Object.values(lienzo.selected_nodes || {})[0];
  const oSel = sel ? ranuraImagen(sel.outputs) : -1;
  const iNuevo = ranuraImagen(nuevo.inputs), oNuevo = ranuraImagen(nuevo.outputs);
  E.cargando = true;
  if (sel && oSel >= 0 && iNuevo >= 0) {
    const siguiente = vecino(sel, +1);
    nuevo.pos = [sel.pos[0] + sel.size[0] + 50, sel.pos[1]];
    grafo.add(nuevo);
    if (siguiente && oNuevo >= 0) {
      const iSig = ranuraImagen(siguiente.inputs);
      // hace sitio desplazando lo que viene detrás
      const dx = nuevo.size[0] + 50;
      for (const n of grafo._nodes) if (n !== nuevo && n.pos[0] >= nuevo.pos[0] - 5) n.pos[0] += dx;
      sel.connect(oSel, nuevo, iNuevo);
      nuevo.connect(oNuevo, siguiente, iSig);
    } else {
      sel.connect(oSel, nuevo, iNuevo);
    }
  } else {
    const c = lienzo.convertOffsetToCanvas([lienzo.canvas.width / 2, lienzo.canvas.height / 2]);
    nuevo.pos = [c[0] - 110, c[1] - 100];
    grafo.add(nuevo);
  }
  E.cargando = false;
  lienzo.selectNode(nuevo);
  cambio();
}

grafo.onNodeAdded = () => cambio();
grafo.onNodeRemoved = (n) => { delete E.resultados[n.id]; if (E.visor.nodo === n.id) seleccionar(null); cambio(); };

lienzo.getMenuOptions = function () {
  return [
    { content: "Añadir proceso", has_submenu: true, callback: LGraphCanvas.onMenuAdd },
    { content: "Encajar el diagrama en la vista", callback: () => encajar() },
  ];
};

function encajar() {
  if (!grafo._nodes.length) return;
  let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
  for (const n of grafo._nodes) {
    x1 = Math.min(x1, n.pos[0]); y1 = Math.min(y1, n.pos[1] - 30);
    x2 = Math.max(x2, n.pos[0] + n.size[0]); y2 = Math.max(y2, n.pos[1] + n.size[1]);
  }
  const w = lienzo.canvas.width, h = lienzo.canvas.height - 30;
  const k = Math.min(1, (w - 40) / (x2 - x1), (h - 40) / (y2 - y1));
  lienzo.ds.scale = k;
  lienzo.ds.offset[0] = (w / k - (x2 - x1)) / 2 - x1;
  lienzo.ds.offset[1] = (h / k - (y2 - y1)) / 2 - y1;
  lienzo.setDirty(true, true);
}

// ------------------------------------------------------------------ guardar y cargar flujos

function exportar() {
  const nodos = grafo._nodes.map((n) => ({
    id: n.id, tipo: n.type, pos: [Math.round(n.pos[0]), Math.round(n.pos[1])],
    tam: [Math.round(n.size[0]), Math.round(n.size[1])], params: { ...n.properties }, activo: n.activo !== false,
  }));
  const enlaces = [];
  for (const l of Object.values(grafo.links)) {
    if (!l) continue;
    const o = grafo.getNodeById(l.origin_id), d = grafo.getNodeById(l.target_id);
    if (o && d) enlaces.push([o.id, o.outputs[l.origin_slot].name, d.id, d.inputs[l.target_slot].name]);
  }
  return { version: 1, nombre: E.flujoNombre, nodos, enlaces };
}

function importar(flujo) {
  E.cargando = true;
  grafo.clear();
  E.resultados = {};
  const desconocidos = [];
  for (const n of flujo.nodos || []) {
    const nodo = LiteGraph.createNode(n.tipo);
    if (!nodo) { desconocidos.push(n.tipo); continue; }
    nodo.id = n.id;
    nodo.pos = [n.pos ? n.pos[0] : 40, n.pos ? n.pos[1] : 40];
    grafo.add(nodo);
    ponerParams(nodo, n.params || {});
    const minimo = nodo.computeSize();
    nodo.size = n.tam ? [Math.max(n.tam[0], minimo[0]), Math.max(n.tam[1], minimo[1])] : minimo;
    if (n.activo === false) { nodo.activo = false; nodo.color = "#3a3a3a"; nodo.bgcolor = "#2a2a2a"; nodo.title += "  ⏸"; }
  }
  for (const [o, salida, d, entrada] of flujo.enlaces || []) {
    const no = grafo.getNodeById(o), nd = grafo.getNodeById(d);
    if (!no || !nd) continue;
    const so = no.outputs.findIndex((s) => s.name === salida), sd = nd.inputs.findIndex((s) => s.name === entrada);
    if (so >= 0 && sd >= 0) no.connect(so, nd, sd);
  }
  E.cargando = false;
  if (desconocidos.length) avisar("Este flujo usa procesos que no están instalados: " + [...new Set(desconocidos)].join(", "), "error", 9000);
  encajar();
  seleccionar(null);
  cambio();
}

async function listarFlujos() {
  const { flujos } = await api("/api/flujos");
  const sel = $("#flujos");
  sel.innerHTML = '<option value="">— abrir flujo —</option>' +
    flujos.map((f) => `<option value="${escapar(f.nombre)}" title="${escapar(f.descripcion)}">${escapar(f.nombre)}</option>`).join("");
  sel.value = flujos.some((f) => f.nombre === E.flujoNombre) ? E.flujoNombre : "";
  return flujos;
}

async function abrirFlujo(nombre) {
  const flujo = await api("/api/flujos/" + encodeURIComponent(nombre));
  E.flujoNombre = nombre;
  localStorage.setItem("lp.flujo", nombre);
  importar(flujo);
  $("#flujos").value = nombre;
}

async function guardarFlujo() {
  const nombre = await pedirTexto("Guardar flujo", "Se guarda como fichero JSON en la carpeta «flujos».", E.flujoNombre || "Mi flujo");
  if (!nombre) return;
  try {
    const r = await api("/api/flujos/" + encodeURIComponent(nombre), { method: "PUT", json: exportar() });
    E.flujoNombre = r.guardado;
    localStorage.setItem("lp.flujo", r.guardado);
    await listarFlujos();
    avisar(`Flujo «${r.guardado}» guardado`, "bien");
  } catch (e) { avisar("No se pudo guardar: " + e.message, "error"); }
}

function flujoNuevo() {
  E.flujoNombre = "";
  importar({ nodos: [
    { id: 1, tipo: "entrada/pagina", pos: [40, 80], params: {} },
    { id: 2, tipo: "salida/guardar", pos: [340, 80], params: {} },
  ], enlaces: [[1, "imagen", 2, "imagen"]] });
  $("#flujos").value = "";
}

// ------------------------------------------------------------------ ejecución

let temporizador = null;

/** Algo cambió en el diagrama: guarda el borrador y, en modo automático, vuelve a ejecutar. */
function cambio() {
  if (E.cargando) return;
  try { localStorage.setItem("lp.borrador", JSON.stringify(exportar())); } catch (_) { /* almacenamiento lleno */ }
  if (!$("#auto").checked) return;
  clearTimeout(temporizador);
  temporizador = setTimeout(ejecutar, 220);
}

async function ejecutar() {
  clearTimeout(temporizador);
  if (E.ejecutando) { E.pendiente = true; return; }
  if (!grafo._nodes.length) return;
  E.ejecutando = true;
  $("#btn-ejecutar").disabled = true;
  $("#tiempo").textContent = "ejecutando…";
  try {
    const r = await api("/api/ejecutar", { json: { flujo: exportar(), pagina: E.pagina } });
    E.resultados = r.nodos;
    const fallos = Object.values(r.nodos).filter((n) => n.estado === "error").length;
    $("#tiempo").textContent = `${Math.round(r.ms)} ms` + (fallos ? ` · ${fallos} con error` : "");
    for (const n of grafo._nodes) cargarMiniatura(n);
    if (E.visor.nodo == null || !grafo.getNodeById(E.visor.nodo)) seleccionar(nodoFinal());
    else actualizarVisor();
  } catch (e) {
    $("#tiempo").textContent = "error";
    avisar(e.message, "error", 8000);
  } finally {
    E.ejecutando = false;
    $("#btn-ejecutar").disabled = false;
    lienzo.setDirty(true, true);
    if (E.pendiente) { E.pendiente = false; ejecutar(); }
  }
}

function primeraImagen(id) {
  const r = E.resultados[id];
  if (!r) return null;
  const par = Object.entries(r.salidas).find(([, s]) => s.tipo === "imagen");
  return par ? par[0] : null;
}

function cargarMiniatura(nodo) {
  const r = E.resultados[nodo.id], salida = primeraImagen(nodo.id);
  if (!r || !salida) { nodo._mini = null; nodo._miniClave = ""; return; }
  const clave = r.clave + ":" + salida;
  if (nodo._miniClave === clave) return;
  const img = new Image();
  img.onload = () => lienzo.setDirty(true, true);
  img.src = `/api/salida/${nodo.id}/${encodeURIComponent(salida)}?max=360&v=${clave}`;
  nodo._mini = img;
  nodo._miniClave = clave;
}

/** Nodo cuyo resultado se enseña cuando no hay nada seleccionado: el de salida o el último de la cadena. */
function nodoFinal() {
  const salidas = grafo._nodes.filter((n) => n.type.startsWith("salida/"));
  if (salidas.length) return salidas[0].id;
  const hojas = grafo._nodes.filter((n) => !(n.outputs || []).some((o) => (o.links || []).length));
  return hojas.length ? hojas[hojas.length - 1].id : null;
}

// ------------------------------------------------------------------ visor

const V = E.visor;
const vLienzo = $("#visor-lienzo");
const vCtx = vLienzo.getContext("2d");

function seleccionar(id) {
  V.nodo = id;
  V.salida = id != null ? primeraImagen(id) : null;
  actualizarVisor();
}

lienzo.onNodeSelected = (n) => seleccionar(n.id);

function actualizarVisor() {
  const nodo = V.nodo != null ? grafo.getNodeById(V.nodo) : null;
  const r = nodo ? E.resultados[nodo.id] : null;
  const spec = nodo ? E.specs[nodo.type] : null;
  $("#visor-titulo").textContent = nodo ? spec.titulo : "Sin selección";

  // Pestañas de las salidas de imagen
  const imagenes = r ? Object.entries(r.salidas).filter(([, s]) => s.tipo === "imagen").map(([k]) => k) : [];
  if (!imagenes.includes(V.salida)) V.salida = imagenes[0] || null;
  $("#visor-salidas").innerHTML = imagenes.length > 1
    ? imagenes.map((k) => `<button data-salida="${escapar(k)}" class="${k === V.salida ? "activa" : ""}">${escapar(k)}</button>`).join("") : "";

  // Imagen
  const clave = r && V.salida ? `${nodo.id}/${V.salida}/${r.clave}` : "";
  if (clave !== V.clave) {
    V.clave = clave;
    if (!clave) { V.img = null; dibujarVisor(); }
    else {
      const url = `/api/salida/${nodo.id}/${encodeURIComponent(V.salida)}?v=${r.clave}`;
      const img = new Image();
      img.onload = () => {
        if (V.clave !== clave) return;
        const mismoTam = V.img && V.img.naturalWidth === img.naturalWidth && V.img.naturalHeight === img.naturalHeight;
        V.img = img;
        if (!mismoTam || V.ajustado) ajustarVisor(); else dibujarVisor();   // conserva el zoom al retocar parámetros
      };
      img.src = url;
      $("#btn-descargar").href = url;
      $("#btn-descargar").download = `${(E.pagina || "pagina").replace(/\.[^.]+$/, "")}_${spec.titulo}_${V.salida}.png`.replace(/[^\w.\-áéíóúñ]+/gi, "_");
    }
  }
  $("#visor-vacio").hidden = !!clave;
  $("#visor-vacio").textContent = nodo ? (r ? "Este nodo no produce ninguna imagen" : "Pulsa «Ejecutar» para ver el resultado")
    : "Selecciona un nodo para ver su resultado";

  // Información
  let html = "";
  if (nodo) {
    if (r) {
      const sal = V.salida ? r.salidas[V.salida] : null;
      const partes = [];
      if (r.estado === "ok") partes.push(r.cache ? "resultado en caché" : `${Math.round(r.ms)} ms`);
      if (r.estado === "desactivado") partes.push("desactivado");
      if (r.gpu) partes.push("ejecutado en GPU");
      if (sal) partes.push(`${sal.ancho} × ${sal.alto} px, ${sal.canales === 1 ? "grises" : "color"}`);
      html += `<div class="estado tenue">${partes.join(" · ")}</div>`;
      if (r.estado === "error" || r.estado === "omitido") html += `<p class="error">${escapar(r.error)}</p>`;
      for (const a of r.avisos || []) html += `<p class="error">${escapar(a)}</p>`;
      for (const [nombre, s] of Object.entries(r.salidas)) {
        if (s.tipo !== "datos") continue;
        html += `<h4>${escapar(nombre)}</h4><table>` + Object.entries(s.valor).map(([k, v]) => {
          const celda = v === true ? '<span class="dato-si">sí</span>' : v === false ? '<span class="dato-no">no</span>'
            : escapar(typeof v === "object" ? JSON.stringify(v) : v);
          return `<tr><td>${escapar(k)}</td><td>${celda}</td></tr>`;
        }).join("") + "</table>";
      }
    }
    html += `<p>${escapar(spec.descripcion)}</p>`;
    const conAyuda = spec.params.filter((p) => p.ayuda);
    if (conAyuda.length) {
      html += "<h4>Parámetros</h4><table>" + conAyuda.map((p) =>
        `<tr><td>${escapar(p.etiqueta)}</td><td>${escapar(p.ayuda)}</td></tr>`).join("") + "</table>";
    }
  }
  $("#visor-info").innerHTML = html;
}

function ajustarVisor() {
  const img = V.verOriginal && V.original ? V.original : V.img;
  if (!img) { dibujarVisor(); return; }
  const w = vLienzo.clientWidth, h = vLienzo.clientHeight;
  V.zoom = Math.min(w / img.naturalWidth, h / img.naturalHeight) * 0.97;
  V.x = (w - img.naturalWidth * V.zoom) / 2;
  V.y = (h - img.naturalHeight * V.zoom) / 2;
  V.ajustado = true;
  dibujarVisor();
}

function dibujarVisor() {
  const dpr = window.devicePixelRatio || 1;
  const w = vLienzo.clientWidth, h = vLienzo.clientHeight;
  if (vLienzo.width !== Math.round(w * dpr) || vLienzo.height !== Math.round(h * dpr)) {
    vLienzo.width = Math.round(w * dpr); vLienzo.height = Math.round(h * dpr);
  }
  vCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  vCtx.clearRect(0, 0, w, h);
  const img = V.verOriginal && V.original && V.original.complete ? V.original : V.img;
  if (!img) { $("#visor-zoom").textContent = ""; return; }
  vCtx.imageSmoothingEnabled = V.zoom < 2;       // de cerca se ven los píxeles tal cual
  vCtx.imageSmoothingQuality = "high";
  // El original puede tener otro tamaño (tras recortar o ampliar): se encaja en el mismo hueco
  const base = V.img || img;
  const kx = base.naturalWidth / img.naturalWidth, ky = base.naturalHeight / img.naturalHeight;
  vCtx.drawImage(img, V.x, V.y, img.naturalWidth * V.zoom * kx, img.naturalHeight * V.zoom * ky);
  $("#visor-zoom").textContent = (V.verOriginal ? "ORIGINAL · " : "") + Math.round(V.zoom * 100) + " %";
}

vLienzo.addEventListener("wheel", (e) => {
  if (!V.img) return;
  e.preventDefault();
  const r = vLienzo.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
  const nuevo = Math.min(16, Math.max(0.02, V.zoom * Math.exp(-e.deltaY * 0.0015)));
  V.x = mx - (mx - V.x) * (nuevo / V.zoom);
  V.y = my - (my - V.y) * (nuevo / V.zoom);
  V.zoom = nuevo;
  V.ajustado = false;
  dibujarVisor();
}, { passive: false });

let arrastre = null;
vLienzo.addEventListener("pointerdown", (e) => { arrastre = [e.clientX - V.x, e.clientY - V.y]; vLienzo.setPointerCapture(e.pointerId); });
vLienzo.addEventListener("pointermove", (e) => {
  if (!arrastre) return;
  V.x = e.clientX - arrastre[0]; V.y = e.clientY - arrastre[1]; V.ajustado = false;
  dibujarVisor();
});
vLienzo.addEventListener("pointerup", () => { arrastre = null; });
vLienzo.addEventListener("dblclick", ajustarVisor);

function verOriginal(si) {
  if (si && !V.original && E.pagina) {
    V.original = new Image();
    V.original.onload = dibujarVisor;
    V.original.src = "/api/pagina/" + encodeURIComponent(E.pagina);
  }
  V.verOriginal = si;
  $("#btn-original").classList.toggle("primario", si);
  dibujarVisor();
}

$("#btn-original").addEventListener("pointerdown", () => verOriginal(true));
for (const ev of ["pointerup", "pointerleave"]) $("#btn-original").addEventListener(ev, () => verOriginal(false));
$("#btn-ajustar").onclick = ajustarVisor;
$("#btn-100").onclick = () => {
  if (!V.img) return;
  const w = vLienzo.clientWidth, h = vLienzo.clientHeight;
  const cx = (w / 2 - V.x) / V.zoom, cy = (h / 2 - V.y) / V.zoom;
  V.zoom = 1; V.x = w / 2 - cx; V.y = h / 2 - cy; V.ajustado = false;
  dibujarVisor();
};
$("#visor-salidas").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-salida]");
  if (b) { V.salida = b.dataset.salida; actualizarVisor(); }
});

// Divisor arrastrable entre el diagrama y el visor
(() => {
  const visor = $("#visor"), divisor = $("#divisor");
  const guardado = Number(localStorage.getItem("lp.visor"));
  if (guardado > 200) visor.style.width = guardado + "px";
  let activo = false;
  divisor.addEventListener("pointerdown", (e) => { activo = true; divisor.setPointerCapture(e.pointerId); });
  divisor.addEventListener("pointermove", (e) => {
    if (!activo) return;
    const ancho = Math.min(window.innerWidth - 520, Math.max(300, window.innerWidth - e.clientX));
    visor.style.width = ancho + "px";
    ajustarLienzo();
  });
  divisor.addEventListener("pointerup", () => {
    activo = false;
    localStorage.setItem("lp.visor", parseInt(visor.style.width, 10));
    if (V.ajustado) ajustarVisor();
  });
})();

// ------------------------------------------------------------------ páginas de entrada

async function listarPaginas(elegir) {
  const r = await api("/api/paginas");
  E.paginas = r.paginas;
  const sel = $("#pagina");
  sel.innerHTML = r.paginas.length ? r.paginas.map((p) => `<option>${escapar(p)}</option>`).join("")
    : '<option value="">(carpeta sin imágenes)</option>';
  sel.title = "Carpeta de entrada: " + r.carpeta;
  const deseada = elegir || localStorage.getItem("lp.pagina");
  fijarPagina(r.paginas.includes(deseada) ? deseada : r.paginas[0] || null, false);
  return r;
}

function fijarPagina(nombre, lanzar = true) {
  E.pagina = nombre;
  V.original = null;
  if (nombre) { $("#pagina").value = nombre; localStorage.setItem("lp.pagina", nombre); }
  if (lanzar) ejecutar();
}

function pasoPagina(d) {
  if (!E.paginas.length) return;
  const i = Math.max(0, E.paginas.indexOf(E.pagina)) + d;
  fijarPagina(E.paginas[(i + E.paginas.length) % E.paginas.length]);
}

$("#pagina").onchange = (e) => fijarPagina(e.target.value);
$("#pag-anterior").onclick = () => pasoPagina(-1);
$("#pag-siguiente").onclick = () => pasoPagina(+1);
$("#btn-subir").onclick = () => $("#fichero").click();
$("#fichero").onchange = async (e) => {
  if (!e.target.files.length) return;
  const datos = new FormData();
  for (const f of e.target.files) datos.append("ficheros", f);
  try {
    const r = await api("/api/subir", { method: "POST", body: datos });
    await listarPaginas(r.guardados[0]);
    ejecutar();
  } catch (err) { avisar("No se pudo subir: " + err.message, "error"); }
  e.target.value = "";
};
$("#btn-carpeta").onclick = async () => {
  const estado = await api("/api/estado");
  const ruta = await pedirTexto("Carpeta de entrada", "Ruta de la carpeta con las páginas de prueba, en este equipo.", estado.carpeta_entrada);
  if (!ruta) return;
  try {
    await api("/api/carpeta", { json: { ruta } });
    await listarPaginas();
    ejecutar();
  } catch (err) { avisar(err.message, "error"); }
};

// ------------------------------------------------------------------ paleta

function pintarPaleta() {
  const filtro = $("#buscar").value.trim().toLowerCase();
  const cont = $("#lista-nodos");
  cont.innerHTML = "";
  const cats = { ...E.categorias };
  for (const s of Object.values(E.specs)) if (!(s.categoria in cats)) cats[s.categoria] = s.categoria;
  for (const [cat, nombre] of Object.entries(cats)) {
    const lista = Object.values(E.specs).filter((s) => s.categoria === cat &&
      (!filtro || (s.titulo + " " + s.descripcion + " " + s.tipo).toLowerCase().includes(filtro)));
    if (!lista.length) continue;
    const h = document.createElement("div");
    h.className = "categoria";
    h.textContent = nombre;
    cont.append(h);
    for (const s of lista) {
      const b = document.createElement("button");
      b.className = "proceso";
      b.title = s.descripcion;
      b.innerHTML = `<span>${escapar(s.titulo)}</span>` + (s.gpu ? '<span class="etiqueta-gpu">GPU</span>' : "");
      b.onclick = () => anadirNodo(s.tipo);
      cont.append(b);
    }
  }
}
$("#buscar").oninput = pintarPaleta;

async function cargarNodos(recargar = false) {
  const r = await api(recargar ? "/api/nodos/recargar" : "/api/nodos", recargar ? { method: "POST" } : {});
  E.categorias = r.categorias;
  registrarNodos(r.nodos);
  pintarPaleta();
  for (const [origen, error] of Object.entries(r.errores || {})) {
    avisar(`No se pudo cargar ${origen}: ${error.trim().split("\n").pop()}`, "error", 12000);
  }
  return r;
}

$("#btn-recargar").onclick = async () => {
  const flujo = exportar();
  try {
    const r = await cargarNodos(true);
    importar(flujo);
    avisar(`${r.nodos.length} procesos disponibles`, "bien");
  } catch (e) { avisar("Error al recargar: " + e.message, "error"); }
};

// ------------------------------------------------------------------ dispositivo

async function pintarEstado(estado) {
  estado = estado || await api("/api/estado");
  $("#dispositivo").value = estado.modo;
  const chip = $("#chip-gpu");
  chip.classList.toggle("gpu", estado.gpu_en_uso);
  if (estado.cuda) {
    chip.textContent = estado.gpu_en_uso ? estado.gpu_nombre.replace(/^NVIDIA (GeForce )?/, "") : "GPU sin usar";
    chip.title = `${estado.gpu_nombre} — ${estado.gpu_vram_libre_mb} MB libres de ${estado.gpu_vram_mb} MB\n` +
      `PyTorch ${estado.torch} · ONNX: ${estado.onnx_proveedores.join(", ")}\nOpenCL en OpenCV: ${estado.opencl_activo ? "activo" : "no"}`;
  } else {
    chip.textContent = "CPU";
    chip.title = `PyTorch no ve ninguna GPU${estado.torch ? " (PyTorch " + estado.torch + ")" : " (PyTorch no instalado)"}.\n` +
      `OpenCL en OpenCV: ${estado.opencl_disponible ? estado.opencl_dispositivo : "no disponible"}\nEjecuta «diagnostico» para más detalle.`;
  }
}

$("#dispositivo").onchange = async (e) => {
  try {
    await pintarEstado(await api("/api/dispositivo", { json: { modo: e.target.value } }));
    ejecutar();
  } catch (err) { avisar(err.message, "error"); }
};

// ------------------------------------------------------------------ lote

let sondeo = null;

async function pintarLote() {
  const p = await api("/api/lote");
  const hayDatos = p.activo || p.total > 0 || p.mensaje;
  $("#lote-progreso").hidden = !hayDatos;
  $("#lote-barra").max = Math.max(1, p.total);
  $("#lote-barra").value = p.hechas;
  $("#lote-texto").textContent = p.activo
    ? `${p.hechas} de ${p.total} · ${p.paginas_por_minuto} págs./min · ${p.actual}`
    : (p.mensaje ? `${p.mensaje}: ${p.hechas} de ${p.total} páginas en ${p.segundos} s, ${p.errores} con error. Resultados en ${p.carpeta_salida}` : "");
  $("#lote-errores").textContent = p.ultimos_errores.join("\n");
  $("#lote-empezar").disabled = p.activo;
  $("#lote-cancelar").hidden = !p.activo;
  if (!p.activo && sondeo) { clearInterval(sondeo); sondeo = null; }
  return p;
}

$("#btn-lote").onclick = async () => {
  const estado = await api("/api/estado");
  if (!$("#lote-entrada").value) $("#lote-entrada").value = estado.carpeta_entrada;
  if (!$("#lote-salida").value) $("#lote-salida").value = estado.carpeta_salida;
  await pintarLote();
  $("#dlg-lote").showModal();
};
$("#lote-cerrar").onclick = () => $("#dlg-lote").close();
$("#lote-cancelar").onclick = () => api("/api/lote/cancelar", { method: "POST" });
$("#lote-empezar").onclick = async () => {
  try {
    await api("/api/lote", { json: { flujo: exportar(), entrada: $("#lote-entrada").value,
      salida: $("#lote-salida").value, recursivo: $("#lote-recursivo").checked } });
    await pintarLote();
    if (!sondeo) sondeo = setInterval(pintarLote, 1000);
  } catch (e) {
    $("#lote-progreso").hidden = false;
    $("#lote-errores").textContent = e.message;
  }
};

// ------------------------------------------------------------------ barra y teclado

$("#btn-ejecutar").onclick = ejecutar;
$("#auto").onchange = (e) => { localStorage.setItem("lp.auto", e.target.checked ? "1" : "0"); if (e.target.checked) ejecutar(); };
$("#btn-guardar").onclick = guardarFlujo;
$("#btn-nuevo").onclick = flujoNuevo;
$("#flujos").onchange = (e) => { if (e.target.value) abrirFlujo(e.target.value).catch((err) => avisar(err.message, "error")); };

document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea, select") || document.querySelector("dialog[open]")) return;
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); ejecutar(); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); guardarFlujo(); }
  else if (e.key.toLowerCase() === "o" && !e.repeat) verOriginal(true);
  else if (e.key.toLowerCase() === "f") ajustarVisor();
  else if (e.key === "1") $("#btn-100").click();
});
document.addEventListener("keyup", (e) => { if (e.key.toLowerCase() === "o") verOriginal(false); });

// ------------------------------------------------------------------ arranque

(async function iniciar() {
  ajustarLienzo();
  try {
    $("#auto").checked = localStorage.getItem("lp.auto") !== "0";
    await cargarNodos();
    await pintarEstado();
    await listarPaginas();
    const flujos = await listarFlujos();
    const borrador = localStorage.getItem("lp.borrador");
    const ultimo = localStorage.getItem("lp.flujo");
    if (borrador) {
      let f;
      try { f = JSON.parse(borrador); } catch (_) { f = { nodos: [], enlaces: [] }; localStorage.removeItem("lp.borrador"); }
      E.flujoNombre = f.nombre || "";
      importar(f);
      $("#flujos").value = flujos.some((x) => x.nombre === E.flujoNombre) ? E.flujoNombre : "";
    } else if (flujos.length) {
      await abrirFlujo(flujos.some((x) => x.nombre === ultimo) ? ultimo : flujos[0].nombre);
    } else {
      flujoNuevo();
    }
    if (!$("#auto").checked) ejecutar();
  } catch (e) {
    avisar("No se pudo iniciar: " + e.message, "error", 15000);
  }
})();
