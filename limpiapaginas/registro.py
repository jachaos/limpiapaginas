"""Registro de nodos.

Un *nodo* es una función de Python decorada con ``@nodo``. El decorador declara
sus entradas, salidas y parámetros; con eso la interfaz dibuja la caja, sus
conexiones y sus controles sin escribir nada de JavaScript.

Ejemplo mínimo (ver ``nodos_extra/ejemplo.py``)::

    @nodo("mis_nodos/invertir", titulo="Invertir",
          entradas={"imagen": IMAGEN}, salidas={"imagen": IMAGEN})
    def invertir(ctx, imagen):
        return 255 - imagen
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# Tipos de conexión. La interfaz solo deja unir salidas y entradas del mismo tipo.
IMAGEN = "imagen"   # numpy uint8, HxW (grises) o HxWx3 (BGR). Las máscaras también: blanco = seleccionado
DATOS = "datos"     # diccionario con resultados (¿hay texto?, ángulo…)


@dataclass
class Param:
    nombre: str
    defecto: Any
    etiqueta: str = ""
    ayuda: str = ""
    tipo: str = "texto"

    def normalizar(self, valor):
        return self.defecto if valor is None else valor

    def a_json(self) -> dict:
        return {"nombre": self.nombre, "tipo": self.tipo, "defecto": self.defecto,
                "etiqueta": self.etiqueta or self.nombre, "ayuda": self.ayuda}


@dataclass(init=False)
class Entero(Param):
    minimo: int = 0
    maximo: int = 255
    impar: bool = False     # tamaños de ventana: 3, 5, 7…
    tipo: str = "entero"

    def __init__(self, nombre, defecto, minimo=0, maximo=255, *, impar=False, etiqueta="", ayuda=""):
        super().__init__(nombre, defecto, etiqueta, ayuda, "entero")
        self.minimo, self.maximo, self.impar = minimo, maximo, impar

    def normalizar(self, valor):
        try:
            v = int(round(float(valor)))
        except (TypeError, ValueError):
            v = self.defecto
        v = max(self.minimo, min(self.maximo, v))
        if self.impar and v % 2 == 0:
            v = v + 1 if v + 1 <= self.maximo else v - 1
        return v

    def a_json(self):
        return {**super().a_json(), "min": self.minimo, "max": self.maximo, "impar": self.impar}


@dataclass(init=False)
class Decimal(Param):
    minimo: float = 0.0
    maximo: float = 1.0
    paso: float = 0.01
    tipo: str = "decimal"

    def __init__(self, nombre, defecto, minimo=0.0, maximo=1.0, *, paso=0.01, etiqueta="", ayuda=""):
        super().__init__(nombre, defecto, etiqueta, ayuda, "decimal")
        self.minimo, self.maximo, self.paso = minimo, maximo, paso

    def normalizar(self, valor):
        try:
            v = float(valor)
        except (TypeError, ValueError):
            v = self.defecto
        return max(self.minimo, min(self.maximo, v))

    def a_json(self):
        return {**super().a_json(), "min": self.minimo, "max": self.maximo, "paso": self.paso}


@dataclass(init=False)
class Opcion(Param):
    valores: list = field(default_factory=list)
    tipo: str = "opcion"

    def __init__(self, nombre, defecto, valores, *, etiqueta="", ayuda=""):
        super().__init__(nombre, defecto, etiqueta, ayuda, "opcion")
        self.valores = list(valores)

    def normalizar(self, valor):
        return valor if valor in self.valores else self.defecto

    def a_json(self):
        return {**super().a_json(), "valores": self.valores}


@dataclass(init=False)
class Logico(Param):
    tipo: str = "logico"

    def __init__(self, nombre, defecto, *, etiqueta="", ayuda=""):
        super().__init__(nombre, bool(defecto), etiqueta, ayuda, "logico")

    def normalizar(self, valor):
        if isinstance(valor, str):
            return valor.strip().lower() in ("1", "true", "si", "sí", "verdadero")
        return self.defecto if valor is None else bool(valor)


@dataclass(init=False)
class Texto(Param):
    tipo: str = "texto"

    def __init__(self, nombre, defecto="", *, etiqueta="", ayuda=""):
        super().__init__(nombre, defecto, etiqueta, ayuda, "texto")

    def normalizar(self, valor):
        return self.defecto if valor is None else str(valor)


@dataclass
class Nodo:
    tipo: str                       # "categoria/nombre", identificador estable guardado en los flujos
    titulo: str
    funcion: Callable
    entradas: dict[str, str]
    salidas: dict[str, str]
    params: list[Param]
    opcionales: tuple[str, ...] = ()
    descripcion: str = ""
    gpu: bool = False               # tiene implementación en GPU (PyTorch/ONNX)
    cache: bool = True

    @property
    def categoria(self) -> str:
        return self.tipo.split("/")[0]

    def normalizar_params(self, crudos: dict | None) -> dict:
        crudos = crudos or {}
        return {p.nombre: p.normalizar(crudos.get(p.nombre)) for p in self.params}

    def a_json(self) -> dict:
        return {
            "tipo": self.tipo, "titulo": self.titulo, "categoria": self.categoria,
            "descripcion": self.descripcion, "gpu": self.gpu,
            "entradas": [{"nombre": n, "tipo": t, "opcional": n in self.opcionales}
                         for n, t in self.entradas.items()],
            "salidas": [{"nombre": n, "tipo": t} for n, t in self.salidas.items()],
            "params": [p.a_json() for p in self.params],
        }


REGISTRO: dict[str, Nodo] = {}

# Orden y nombre visible de las categorías en la paleta
CATEGORIAS = {
    "entrada": "Entrada",
    "salida": "Salida",
    "color": "Color y contraste",
    "fondo": "Fondo y papel",
    "ruido": "Ruido y filtros",
    "binarizar": "Binarización",
    "geometria": "Geometría",
    "texto": "Detección de texto",
    "mascara": "Máscaras y mezcla",
    "neuronal": "Redes neuronales",
}


def nodo(tipo: str, *, titulo: str, entradas: dict | None = None, salidas: dict | None = None,
         params: list[Param] | None = None, opcionales: tuple[str, ...] = (), descripcion: str = "",
         gpu: bool = False, cache: bool = True):
    """Decorador que registra una función como nodo del diagrama.

    La función recibe ``ctx`` (ver ``motor.Contexto``), después una entrada por
    cada clave de ``entradas`` y un argumento por cada parámetro. Devuelve un
    valor si tiene una salida, o una tupla en el orden de ``salidas``.
    """
    def decorar(funcion):
        REGISTRO[tipo] = Nodo(tipo, titulo, funcion, dict(entradas or {}), dict(salidas or {}),
                              list(params or []), tuple(opcionales),
                              descripcion or (funcion.__doc__ or "").strip(), gpu, cache)
        return funcion
    return decorar
