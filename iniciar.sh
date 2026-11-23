#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then echo "Ejecuta primero ./instalar.sh"; exit 1; fi
exec .venv/bin/python -m limpiapaginas servidor "$@"
