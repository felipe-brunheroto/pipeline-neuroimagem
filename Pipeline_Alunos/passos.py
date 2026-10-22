#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
passos.py — Menu guiado (em portugues) do pipeline CBR para alunos.

NAO e preciso decorar comandos: este menu explica cada etapa em portugues,
pergunta se voce quer continuar e roda o passo para voce. Os programas "de
verdade" ficam na pasta "code" do projeto (um nivel acima desta) e nao sao
alterados.

ONDE ESTAO OS DADOS? O passo 1 (converter DICOM) precisa saber em que pasta
estao os exames. Por padrao ele procura na pasta "Patient Files", ao lado do
projeto. Se os seus dados estiverem em OUTRO lugar (outro HD, pen-drive,
Downloads, etc.), use o item "D" do menu: voce pode ARRASTAR a pasta para a
janela do Terminal e o menu confere se ela tem os exames antes de rodar. O
local escolhido fica guardado em local_dos_dados.txt para a proxima vez.

Como abrir: de DOIS cliques em "iniciar.command" (na mesma pasta), ou rode
    python3 passos.py
"""

import os
import sys
import subprocess
from pathlib import Path

# A pasta do projeto e a pasta de cima (Pipeline_Alunos fica dentro do projeto).
RAIZ = Path(__file__).resolve().parent.parent
AQUI = Path(__file__).resolve().parent

# Onde o passo 1 procura os exames por padrao, e onde guardamos a escolha do
# aluno (so um caminho de pasta; NAO contem dados de paciente).
PADRAO_DADOS = RAIZ / "Patient Files"
ARQ_LOCAL = AQUI / "local_dos_dados.txt"

# Cada passo: (rotulo, titulo, script, explicacao em portugues, precisa_freesurfer)
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
    ("3", "Montar o conjunto BIDS (anat/func)", "make_bids.py",
     "Organiza as imagens cruas no formato BIDS (padrao internacional, FAIR), "
     "com nomes e metadados corretos, na pasta bids/. Cuida das estruturais "
     "(T1/T2/FLAIR) e, quando houver, das funcionais (BOLD). Nao mexe no que ja "
     "existe.",
     False),
    ("4", "Montar derivados + sourcedata", "make_derivatives.py",
     "Coloca os mapas derivados do scanner (ADC, TRACEW, SWI, perfusao) em "
     "bids/derivatives/ e prepara ASL e o field map (fase) em bids/sourcedata/ "
     "com instrucoes do que ainda falta preencher. Nao inventa valores.",
     False),
    ("5", "Remocao facial (defacing)", "deface_anat.py",
     "Remove o rosto das imagens estruturais (T1/T2/FLAIR) para que ninguem "
     "possa reconstruir a face. Le de bids/ e grava copias em "
     "bids/derivatives/defaced/. Mantem os originais intactos. Precisa do "
     "FreeSurfer (roda no computador de Recon). Rode DEPOIS do passo 3.",
     True),
    ("6", "FreeSurfer (recon longitudinal)", "run_recon_long.py",
     "Reconstroi o cerebro de cada exame com o FreeSurfer 8 e compara cada "
     "pessoa ao longo do tempo. E a etapa MAIS DEMORADA (horas por exame), por "
     "isso roda em pequenos lotes. Precisa do FreeSurfer instalado.",
     True),
    ("7", "Extrair os volumes (tabelas)", "extract_fs_stats.py",
     "Transforma a saida do FreeSurfer em tabelas simples (volumes e espessura "
     "por regiao do cerebro), prontas para abrir no Excel ou para estatistica. "
     "Este e o produto final desta etapa do treinamento.",
     True),
]

# Apenas o passo 1 (conversao) precisa saber onde estao os DICOM. Os demais leem
# de bids/ (a conversao fica em bids/sourcedata/conversion), entao nao recebem
# caminho.
PASSO_USA_PASTA_DADOS = "dicom_to_bids.py"


# ===========================================================================
#  ONDE ESTAO OS DADOS  -  localizar, conferir e lembrar a pasta dos DICOM
# ===========================================================================

def _curto(caminho):
    """Mostra o caminho de forma curta, trocando a pasta pessoal por '~'."""
    texto = str(caminho)
    casa = str(Path.home())
    return "~" + texto[len(casa):] if texto.startswith(casa) else texto


def _eh_dicom(caminho):
    """
    Diz, de forma rapida, se um arquivo e um DICOM - sem abrir tudo.
    Arquivos DICOM tem as letras 'DICM' no byte 128. So lemos esses 4 bytes,
    o mesmo truque que o programa de verdade usa. Assim ignoramos lixo
    (executaveis, .dll, textos) que vem nos CDs do scanner.
    """
    try:
        with open(caminho, "rb") as f:
            f.seek(128)
            return f.read(4) == b"DICM"
    except Exception:
        return False


def _tem_dicom(pasta, limite=4000):
    """
    True se houver pelo menos UM arquivo DICOM dentro de 'pasta' (em qualquer
    nivel). Para assim que encontra o primeiro, entao o caso comum (pasta cheia
    de DICOM) e quase instantaneo. O 'limite' evita demorar demais em pastas
    enormes que nao tenham DICOM nenhum.
    """
    checados = 0
    for raiz, _dirs, arquivos in os.walk(pasta):
        for nome in arquivos:
            checados += 1
            if checados > limite:
                return False
            if _eh_dicom(os.path.join(raiz, nome)):
                return True
    return False


# Nomes de subpasta que sao "embalagem" do scanner, e NAO codigos de exame.
# Se a unica subpasta com DICOM tiver um destes nomes, quase sempre o aluno
# apontou para DENTRO de um exame (ex.: CBR-003/DICOM) em vez da pasta que
# contem os exames. Sao convencoes de scanner (nao dependem deste estudo).
NOMES_GENERICOS = {"dicom", "dicomdir", "images", "image", "imagens", "img",
                   "dcm", "scans", "scan", "study", "studies", "exam", "data"}


def _exames_em(pasta):
    """Lista as subpastas imediatas de 'pasta' que contem DICOM (os exames)."""
    achados = []
    try:
        nomes = sorted(os.listdir(pasta))
    except Exception:
        return achados
    for nome in nomes:
        sub = os.path.join(pasta, nome)
        if os.path.isdir(sub) and _tem_dicom(sub):
            achados.append(nome)
    return achados


def diagnosticar(pasta):
    """
    Confere uma pasta candidata e explica, em portugues, o que encontrou.
    Devolve um dos estados: 'ok', 'apontou_exame', 'vazia', 'inexistente'.
    """
    pasta = Path(pasta)
    print("\n  Conferindo a pasta de dados:")
    print(f"    {pasta}")

    if not pasta.is_dir():
        print("\n  [x] Essa pasta nao existe (ou nao e uma pasta).")
        print("      Confira o caminho e tente de novo (item D do menu).")
        return "inexistente"

    exames = _exames_em(pasta)
    if exames:
        # Caso classico de engano: uma unica subpasta com nome generico de
        # scanner (ex.: "DICOM") quase sempre quer dizer que apontaram para
        # DENTRO de um exame, nao para a pasta que contem os exames.
        if len(exames) == 1 and exames[0].strip().lower() in NOMES_GENERICOS:
            print(f"\n  [!] Achei DICOM dentro de uma subpasta chamada "
                  f"'{exames[0]}'.")
            print("      Isso costuma ser o conteudo de UM exame, nao a pasta")
            print("      com varios exames. Aponte um nivel ACIMA (a pasta que")
            print("      tem dentro as pastas dos exames, ex.: CBR-003, CBR-052).")
            return "apontou_exame"
        print(f"\n  [ok] Encontrei {len(exames)} pasta(s) de exame com DICOM:")
        for nome in exames[:8]:
            print(f"        - {nome}")
        if len(exames) > 8:
            print(f"        ... e mais {len(exames) - 8}.")
        print("\n  Confira se os nomes acima sao mesmo os seus exames (ex.: "
              "CBR-003).")
        print("  Se NAO forem (ex.: uma pasta 'DICOM' ou nomes estranhos),")
        print("  voce pode ter apontado para o lugar errado - avise o responsavel.")
        return "ok"

    # Nenhuma subpasta-de-exame. Talvez o aluno apontou direto para UM exame.
    if _tem_dicom(pasta):
        print("\n  [!] Esta pasta tem arquivos DICOM soltos, mas nenhuma "
              "subpasta de exame.")
        print("      Parece que voce apontou para UMA pasta de exame (ex.: "
              "CBR-003).")
        print("      Aponte para a pasta que CONTEM as pastas CBR-xxx (um nivel "
              "acima).")
        return "apontou_exame"

    print("\n  [x] Nao encontrei nenhum arquivo DICOM aqui dentro.")
    print("      O esperado e uma pasta com subpastas CBR-xxx, cada uma com os")
    print("      arquivos do scanner. Confira se apontou para o lugar certo.")
    return "vazia"


def _limpar_caminho(texto):
    """
    Limpa o que o aluno colou/arrastou. Ao arrastar uma pasta para o Terminal,
    o macOS coloca aspas (se tiver espacos) ou escapa espacos como '\\ '. Aqui
    desfazemos isso e expandimos o '~' (pasta pessoal).
    """
    t = texto.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        t = t[1:-1]                      # remove aspas externas
    t = t.replace("\\ ", " ")            # desfaz espacos escapados
    return os.path.expanduser(t.strip())


def carregar_local():
    """Le a pasta de dados guardada (se houver). Devolve Path ou None."""
    try:
        if ARQ_LOCAL.is_file():
            txt = ARQ_LOCAL.read_text(encoding="utf-8").strip()
            if txt:
                return Path(txt)
    except Exception:
        pass
    return None


def salvar_local(pasta):
    """Guarda a pasta escolhida em local_dos_dados.txt (so o caminho)."""
    try:
        ARQ_LOCAL.write_text(str(Path(pasta).resolve()) + "\n", encoding="utf-8")
    except Exception as err:
        print(f"  (aviso: nao consegui guardar o local: {err})")


def local_dos_dados():
    """Pasta de dados em uso agora: a guardada, senao o local padrao."""
    salvo = carregar_local()
    return salvo if salvo is not None else PADRAO_DADOS


def escolher_pasta_dados():
    """
    Pergunta ao aluno onde estao os DICOM (arrastar, colar, ou ENTER = padrao),
    confere e, se estiver tudo certo, guarda o local. Devolve (pasta, estado).
    """
    print("\n" + "-" * 68)
    print("  ONDE ESTAO SEUS ARQUIVOS DICOM?")
    print("-" * 68)
    print("  Tres formas (escolha a mais facil):")
    print("   1) ARRASTE a pasta dos dados para esta janela e aperte ENTER")
    print("      (o caminho aparece sozinho - jeito mais facil), ou")
    print("   2) cole/digite o caminho da pasta e aperte ENTER, ou")
    print("   3) aperte ENTER sem nada para usar o local padrao:")
    print(f"      {PADRAO_DADOS}")
    print("")
    try:
        bruto = input("  Pasta de dados> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  (cancelado)")
        return local_dos_dados(), "cancelado"

    escolhida = PADRAO_DADOS if not bruto else Path(_limpar_caminho(bruto))
    estado = diagnosticar(escolhida)

    if estado == "ok":
        salvar_local(escolhida)
        print("\n  [ok] Pronto! Vou usar esta pasta e lembrar dela na proxima")
        print("       vez (guardado em local_dos_dados.txt).")
    else:
        print("\n  Nada foi guardado. Tente de novo pelo item D do menu ou")
        print("  chame o responsavel.")
    return escolhida, estado


def conferir_dados():
    """Item 'D' do menu: mostra a pasta atual, confere e permite trocar."""
    pasta = local_dos_dados()
    print("\n" + "-" * 68)
    print("  PASTA DE DADOS ATUAL")
    print("-" * 68)
    print("  E daqui que o passo 1 vai ler os exames:")
    print(f"    {pasta}")
    estado = diagnosticar(pasta)
    if estado == "ok":
        resp = input("\n  Quer trocar para outra pasta? (s/n): ").strip().lower()
        if resp not in ("s", "sim", "y"):
            print("  Ok, mantendo esta pasta.")
            return
    escolher_pasta_dados()


def garantir_pasta_para_passo1():
    """
    Antes de converter (passo 1), garante que temos uma pasta de dados valida.
    Devolve (pasta, ok). Se nao der para confirmar, ok=False e nao convertemos.
    """
    pasta = local_dos_dados()
    estado = diagnosticar(pasta)
    if estado == "ok":
        return pasta, True
    print("\n  Antes de converter, precisamos apontar a pasta certa.")
    resp = input("  Quer apontar a pasta de dados agora? (s/n): ").strip().lower()
    if resp in ("s", "sim", "y"):
        pasta, estado = escolher_pasta_dados()
        if estado == "ok":
            return pasta, True
    print("\n  Conversao cancelada (pasta de dados nao confirmada).")
    return pasta, False


# ===========================================================================
#  MENU
# ===========================================================================

def cabecalho():
    print("\n" + "=" * 68)
    print("  PIPELINE CBR - menu guiado (do DICOM aos volumes do cerebro)")
    print("=" * 68)
    print("  Mapa:  DICOM -> NIfTI -> desidentificar -> BIDS -> derivados ->")
    print("         remocao facial -> FreeSurfer -> VOLUMES")
    print("  Dica:  faca os passos em ordem (0,1,2,...). Em caso de duvida,")
    print("         leia o GUIA_DO_ALUNO.pdf ou chame o responsavel.")
    print("=" * 68)


def menu():
    cabecalho()
    print(f"\n  Pasta de dados (passo 1): {_curto(local_dos_dados())}")
    print("  (errada ou em outro lugar? use o item D)")
    print("\n  Escolha um numero/letra e aperte ENTER:\n")
    for num, titulo, _script, _exp, fs in PASSOS:
        marca = "  (precisa do FreeSurfer)" if fs else ""
        print(f"   {num}) {titulo}{marca}")
    print("   D) Onde estao meus arquivos (apontar/conferir a pasta de dados)")
    print("   q) Sair")
    print("")


def rodar_passo(num):
    passo = next((p for p in PASSOS if p[0] == num), None)
    if passo is None:
        print("  Opcao invalida. Tente de novo.")
        return
    _num, titulo, script, exp, precisa_fs = passo
    caminho = RAIZ / "code" / script
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

    # Passo 1 precisa saber ONDE estao os DICOM: conferimos (e ajustamos) antes.
    extra_args = []
    if script == PASSO_USA_PASTA_DADOS:
        pasta, ok = garantir_pasta_para_passo1()
        if not ok:
            return
        extra_args = [str(pasta)]

    resp = input("\n  Deseja rodar este passo agora? (s/n): ").strip().lower()
    if resp not in ("s", "sim", "y"):
        print("  Ok, nada foi feito.")
        return

    cmd = [sys.executable, str(caminho)] + extra_args

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
        if escolha in ("d", "dados"):
            conferir_dados()
        else:
            rodar_passo(escolha)
        input("\n  (aperte ENTER para voltar ao menu) ")


if __name__ == "__main__":
    main()
