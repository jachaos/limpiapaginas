#!/usr/bin/env bash
# Instala LimpiaPáginas en Linux: entorno virtual, dependencias y PyTorch/ONNX
# Runtime con GPU si hay una NVIDIA. Se puede repetir sin problema.
#   ./instalar.sh          detecta la GPU
#   ./instalar.sh --cpu    fuerza la instalación solo para CPU
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for c in python3.12 python3.11 python3.13 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then echo "Hace falta Python 3.11 o superior."; exit 1; fi
echo "== Python: $($PY --version)"

[ -d .venv ] || "$PY" -m venv .venv
PIP=".venv/bin/python -m pip"
$PIP install --quiet --upgrade pip
echo "== Instalando dependencias base"
$PIP install --quiet -r requirements.txt

GPU=0
if [ "${1:-}" != "--cpu" ] && command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
  GPU=1
  echo "== GPU detectada: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1)"
else
  echo "== Sin GPU NVIDIA (o instalación forzada a CPU)"
fi

if [ "$GPU" = 1 ]; then
  echo "== Instalando PyTorch con CUDA y ONNX Runtime para GPU"
  # En Linux, el PyTorch de PyPI ya incluye CUDA.
  $PIP install --quiet torch
  $PIP uninstall --quiet -y onnxruntime 2>/dev/null || true
  $PIP install --quiet onnxruntime-gpu || $PIP install --quiet onnxruntime
else
  echo "== Instalando PyTorch y ONNX Runtime para CPU"
  $PIP install --quiet torch --index-url https://download.pytorch.org/whl/cpu \
    || { echo "   (índice de CPU no accesible: se instala el paquete normal)"; $PIP install --quiet torch; }
  $PIP install --quiet onnxruntime
fi

echo
.venv/bin/python -m limpiapaginas diagnostico || true
echo "Listo. Arranca con:  ./iniciar.sh"
