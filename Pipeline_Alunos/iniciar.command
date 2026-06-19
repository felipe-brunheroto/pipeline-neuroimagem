#!/bin/bash
# Dê DOIS cliques neste arquivo para abrir o menu guiado do pipeline.
# (Ele simplesmente entra nesta pasta e abre o menu em portugues.)
cd "$(dirname "$0")" || exit 1
clear
echo "Abrindo o menu guiado do pipeline CBR..."
echo
python3 passos.py
echo
echo "Menu encerrado. Voce pode fechar esta janela."
