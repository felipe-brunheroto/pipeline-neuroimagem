#!/bin/bash
# De DOIS cliques neste arquivo para conferir o computador.
# Ele explica cada item em portugues e so muda algo se voce responder "s".
cd "$(dirname "$0")/.." || exit 1
clear
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
"$PY" code/diagnostico.py --salvar
echo
echo "Pronto. Voce pode fechar esta janela."
read -r -p "(aperte ENTER) " _
