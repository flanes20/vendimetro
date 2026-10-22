#!/bin/bash
# Compila "Libro de Ventas.app" para Mac. Se ejecuta EN una Mac, desde esta carpeta:
#   bash compilar_mac.sh
# Requiere Python 3 (https://www.python.org/downloads/macos/).
set -e
cd "$(dirname "$0")"
python3 -m pip install --upgrade pywebview pyinstaller pillow
python3 -m PyInstaller --noconfirm --windowed --name "Libro de Ventas" \
  --icon icono.png --add-data "web:web" --add-data "icono.ico:." app.py
echo
echo "Listo: dist/Libro de Ventas.app"
echo "Arrástralo a la carpeta Aplicaciones."
