#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
passos.py — Menu guiado (em portugues) do pipeline CBR para alunos.

NAO e preciso decorar comandos: este menu explica cada etapa em portugues,
pergunta se voce quer continuar e roda o passo para voce. Os programas "de
verdade" ficam na pasta do projeto (um nivel acima desta) e nao sao alterados.

Como abrir: de DOIS cliques em "iniciar.command" (na mesma pasta), ou rode
    python3 passos.py
"""

import os
import sys
import subprocess
from pathlib import Path

# A pasta do projeto e a pasta de cima (Pipeline_Alunos fica dentro do projeto).
RAIZ = Path(__file__).resolve().parent.parent

# Cada passo: (rotulo, script, explicacao em portugues, precisa_freesurfer, extra)
PASSOS = [
    ("0", "Checar o ambiente", "check_environment.py",
     "Verifica se as ferramentas (Python, dcm2niix, pydicom) estao instaladas. "
     "Nao muda nada no computador - so confere e avisa. Comece sempre por aqui.",
     False),
    ("1", "Converter DICOM em NIfTI", "dicom_to_bids.py",
     "Le os arquivos DICOM do scanner e os converte para NIfTI (.nii.gz), "
     "organizando em pastas (anat/dwi/fmap/perf/swi/misc). Gera tambem "
     "provenance.csv (de onde veio cada imagem) e validation_report.csv. "
     "NUNCA altera os DICOM originais.",
     False),
    ("2", "Desidentificar (remover identificadores)", "deidentify_sidecars.py",
     "Apaga campos de instituicao/equipamento dos arquivos .json, deixando os "
     "dados de acordo com boas praticas de privacidade. So depois deste passo os "
     "dados podem ser considerados desidentificados.",
     False),
    ("3", "Remocao facial (defacing)", "deface_anat.py",
     "Remove o rosto das imagens estruturais (T1/T2/FLAIR) para que ninguem "
     "possa reconstruir a face. Mantem os originais intactos. Precisa do "
     "FreeSurfer instalado (roda no computador de Recon).",
     True),
    ("4", "Montar o conjunto BIDS", "make_bids.py",
     "Organiza as imagens anatomicas no formato BIDS (padrao internacional, "
     "FAIR), com nomes e metadados corretos, na pasta bids/. Nao mexe no que ja "
     "existe.",
     False),
    ("5", "FreeSurfer (recon longitudinal)", "run_recon_long.py",
     "Reconstroi o cerebro de cada exame com o FreeSurfer 8 e compara cada "
     "pessoa ao longo do tempo. E a etapa MAIS DEMORADA (horas por exame), por "
     "isso roda em pequenos lotes. Precisa do FreeSurfer instalado.",
     True),
    ("6", "Extrair os volumes (tabelas)", "extract_fs_stats.py",
     "Transforma a saida do FreeSurfer em tabelas simples (volumes e espessura "
     "por regiao do cerebro), prontas para abrir no Excel ou para estatistica. "
     "Este e o produto final desta etapa do treinamento.",
     True),
]


def cabecalho():
    print("\n" + "=" * 68)
    print("  PIPELINE CBR - menu guiado (do DICOM aos volumes do cerebro)")
    print("=" * 68)
    print("  Mapa:  DICOM -> NIfTI -> desidentificar -> remocao facial ->")
    print("         BIDS -> FreeSurfer -> VOLUMES")
    print("  Dica:  faca os passos em ordem (0,1,2,...). Em caso de duvida,")
    print("         leia o GUIA_DO_ALUNO.pdf ou chame o responsavel.")
    print("=" * 68)


def menu():
    cabecalho()
    print("\n  Escolha um numero e aperte ENTER:\n")
    for num, titulo, _script, _exp, fs in PASSOS:
        marca = "  (precisa do FreeSurfer)" if fs else ""
        print(f"   {num}) {titulo}{marca}")
    print("   q) Sair")
    print("")


def rodar_passo(num):
    passo = next((p for p in PASSOS if p[0] == num), None)
    if passo is None:
        print("  Opcao invalida. Tente de novo.")
        return
    _num, titulo, script, exp, precisa_fs = passo
    caminho = RAIZ / script
    print("\n" + "-" * 68)
    print(f"  PASSO {num}: {titulo}")
    print("-" * 68)
    print("  " + exp.replace(". ", ".\n  "))
    if not caminho.is_file():
        print(f"\n  [erro] nao encontrei {script} na pasta do projeto.")
        print(f"         esperado em: {caminho}")
        return
    if precisa_fs:
        print("\n  Atencao: este passo precisa do FreeSurfer carregado")
        print("  (no computador de Recon). Se nao estiver, ele vai avisar.")

    resp = input("\n  Deseja rodar este passo agora? (s/n): ").strip().lower()
    if resp not in ("s", "sim", "y"):
        print("  Ok, nada foi feito.")
        return

    cmd = [sys.executable, str(caminho)]

    # O passo 5 (FreeSurfer) roda em lotes: perguntamos quantos em paralelo.
    if script == "run_recon_long.py":
        n = input("  Quantos exames processar em paralelo? "
                  "(recomendado: 2; ENTER usa 2): ").strip()
        n = n if n.isdigit() and int(n) >= 1 else "2"
        cmd += ["--jobs", n]
        print(f"  Rodando em lotes de {n}. Isso pode levar horas - tudo bem.")

    print("\n  >>> rodando... (a saida do programa aparece abaixo)\n")
    try:
        result = subprocess.run(cmd)
        if result.returncode == 0:
            print("\n  ✔ Passo concluido. Veja os resultados na pasta do projeto.")
        else:
            print("\n  ✖ O passo terminou com aviso/erro (codigo "
                  f"{result.returncode}). Leia a mensagem acima; se nao tiver")
            print("    certeza, NAO improvise - chame o responsavel.")
    except KeyboardInterrupt:
        print("\n  (interrompido por voce)")


def main():
    if "--list" in sys.argv:        # modo de teste: so mostra o menu e sai
        menu()
        return
    while True:
        menu()
        try:
            escolha = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Ate logo!")
            return
        if escolha in ("q", "sair", "quit", "exit"):
            print("  Ate logo!")
            return
        rodar_passo(escolha)
        input("\n  (aperte ENTER para voltar ao menu) ")


if __name__ == "__main__":
    main()
