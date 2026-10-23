#!/bin/bash
# Dê DOIS cliques neste arquivo para abrir o menu guiado do pipeline.
# (Ele simplesmente entra nesta pasta e abre o menu em portugues.)
cd "$(dirname "$0")" || exit 1
clear
echo "Abrindo o menu guiado do pipeline de neuroimagem (CBR e COGAGING)..."
echo
# O Python preparado pelo start.command (pasta .venv do projeto), se existir.
PY=python3
[ -x ../.venv/bin/python ] && PY=../.venv/bin/python
"$PY" passos.py
echo
echo "Menu encerrado. Voce pode fechar esta janela."
