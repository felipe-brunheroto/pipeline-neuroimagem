#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
passos.py — Menu guiado (em portugues) do pipeline de neuroimagem para alunos.

NAO e preciso decorar comandos: este menu explica cada etapa em portugues,
pergunta se voce quer continuar e roda o passo para voce. Os programas "de
verdade" ficam na pasta "code" do projeto (um nivel acima desta) e nao sao
alterados.

QUAL ESTUDO? O mesmo pipeline atende mais de um estudo (ex.: CBR e COGAGING).
Cada um tem sua pasta em "projects/". O menu pergunta o estudo ao abrir (item
P troca) e lembra a escolha em projeto_atual.txt.

ONDE ESTAO OS DADOS? Nos estudos que chegam em DICOM (ex.: CBR), o passo 1
precisa saber em que pasta estao os exames. Por padrao ele procura na pasta
"Patient Files" do estudo (projects/<ESTUDO>/Patient Files). Se os seus dados
estiverem em OUTRO lugar (outro HD, pen-drive, Downloads, etc.), use o item
"D" do menu: voce pode ARRASTAR a pasta para a janela do Terminal e o menu
confere se ela tem os exames antes de rodar. O local escolhido fica guardado
em local_dos_dados_<ESTUDO>.txt para a proxima vez. Nos estudos que ja chegam
em BIDS (ex.: COGAGING), o passo 1 importa a pasta
projects/<ESTUDO>/sourcedata/received.

Como abrir: de DOIS cliques em "iniciar.command" (na mesma pasta), ou rode
    python3 passos.py
"""

import json
import os
import sys
import subprocess
from pathlib import Path

# A pasta do projeto e a pasta de cima (Pipeline_Alunos fica dentro do projeto).
RAIZ = Path(__file__).resolve().parent.parent
AQUI = Path(__file__).resolve().parent
PROJETOS = RAIZ / "projects"
ARQ_PROJETO = AQUI / "projeto_atual.txt"

# Estudo em uso (nome da pasta em projects/) e a forma como os dados chegam:
# "dicom" (convertemos) ou "bids" (ja convertidos; importamos). Definidos por
# escolher_projeto() ao abrir o menu.
PROJETO = None
ENTRADA = "dicom"
CONF = {}


def listar_projetos():
    """{nome: configuracao} de cada pasta em projects/ que tem project.json."""
    achados = {}
    for arq in sorted(PROJETOS.glob("*/project.json")):
        try:
            achados[arq.parent.name] = json.loads(arq.read_text(encoding="utf-8"))
        except Exception:
            pass
    return achados


def padrao_dados():
    """Onde o passo 1 procura os exames por padrao (no estudo atual)."""
    return PROJETOS / PROJETO / "Patient Files"


def arq_local():
    """Onde guardamos a pasta escolhida pelo aluno (so um caminho; sem dados)."""
    return AQUI / f"local_dos_dados_{PROJETO}.txt"

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
     "FreeSurfer (roda no computador de Recon). Rode DEPOIS do passo 3 (ou do "
     "passo 1, nos estudos que ja chegam em BIDS).",
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
    ("8", "Lesoes de substancia branca (SAMSEG)", "run_samseg.py",
     "Mede o volume das lesoes (hiperintensidades) de substancia branca com o "
     "SAMSEG, usando o T1 e a FLAIR de cada exame. Alinha a FLAIR ao T1, roda o "
     "SAMSEG e gera a tabela samseg_lesions.tsv (volume em mm3 e % do "
     "intracraniano). Uns 5 minutos por exame. Precisa do FreeSurfer nativo do "
     "Mac Mini. Depois, faca o QC visual que o programa mostra no final.",
     True),
]

# Estudos com sujeitos que ja chegaram convertidos (dcm2bids, antes deste
# pipeline) podem importar esse BIDS com o item I.
PASSO_IMPORTAR = (
    "I", "Importar BIDS ja convertido (dcm2bids)", "import_bids.py",
    "Traz para o estudo os sujeitos que a equipe ja tinha convertido para BIDS "
    "(pasta sourcedata/received do estudo): numera as sessoes por data, apaga "
    "os campos de instituicao/equipamento dos .json e separa as imagens que o "
    "scanner ja processou (ex.: TRACEW). NUNCA altera a copia recebida.",
    False)

# Nos estudos que ja chegam em BIDS, o passo 1 e a importacao, e os passos 2-4
# (desidentificar, montar BIDS, derivados) ja estao incluidos nela.
PASSO_1_BIDS = (
    "1", "Importar o BIDS recebido", "import_bids.py",
    "Copia as imagens que a equipe do estudo entregou (pasta sourcedata/received "
    "do estudo) para bids/, acrescenta a sessao (ses-1), apaga os campos de "
    "instituicao/equipamento dos .json e separa as imagens que o scanner ja "
    "processou (ex.: TRACEW). NUNCA altera a copia recebida.",
    False)
SO_DICOM = {"2", "3", "4"}


def passos_do_projeto():
    """A lista de passos do estudo atual (depende de project.json: como os dados
    chegam e quais etapas o estudo usa)."""
    etapas = CONF.get("steps", {})
    lista = []
    for p in PASSOS:
        if p[0] == "5" and not etapas.get("deface", True):
            continue
        if p[0] == "8" and not etapas.get("samseg", False):
            continue
        if ENTRADA == "bids":
            if p[0] in SO_DICOM:
                continue
            if p[0] == "1":
                p = PASSO_1_BIDS
        lista.append(p)
    if ENTRADA != "bids" and CONF.get("bids_import"):
        lista.append(PASSO_IMPORTAR)
    return lista


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
        if arq_local().is_file():
            txt = arq_local().read_text(encoding="utf-8").strip()
            if txt:
                return Path(txt)
    except Exception:
        pass
    return None


def salvar_local(pasta):
    """Guarda a pasta escolhida em local_dos_dados_<ESTUDO>.txt (so o caminho)."""
    try:
        arq_local().write_text(str(Path(pasta).resolve()) + "\n", encoding="utf-8")
    except Exception as err:
        print(f"  (aviso: nao consegui guardar o local: {err})")


def local_dos_dados():
    """Pasta de dados em uso agora: a guardada, senao o local padrao."""
    salvo = carregar_local()
    return salvo if salvo is not None else padrao_dados()


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
    print(f"      {padrao_dados()}")
    print("")
    try:
        bruto = input("  Pasta de dados> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  (cancelado)")
        return local_dos_dados(), "cancelado"

    escolhida = padrao_dados() if not bruto else Path(_limpar_caminho(bruto))
    estado = diagnosticar(escolhida)

    if estado == "ok":
        salvar_local(escolhida)
        print("\n  [ok] Pronto! Vou usar esta pasta e lembrar dela na proxima")
        print(f"       vez (guardado em {arq_local().name}).")
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

def escolher_projeto(perguntar=False):
    """
    Define o estudo em uso (PROJETO/ENTRADA). Com um so estudo, usa ele. Com
    varios, usa o ultimo escolhido (projeto_atual.txt), ou pergunta quando
    'perguntar' for True ou ainda nao houver escolha.
    """
    global PROJETO, ENTRADA, CONF
    projetos = listar_projetos()
    if not projetos:
        print(f"\n  [erro] nenhum estudo encontrado em {PROJETOS}.")
        print("         Chame o responsavel.")
        sys.exit(1)
    nomes = list(projetos)
    escolhido = None
    if len(nomes) == 1:
        escolhido = nomes[0]
    elif not perguntar and ARQ_PROJETO.is_file():
        salvo = ARQ_PROJETO.read_text(encoding="utf-8").strip()
        escolhido = salvo if salvo in projetos else None
    while escolhido is None:
        print("\n" + "-" * 68)
        print("  QUAL ESTUDO VOCE VAI PROCESSAR?")
        print("-" * 68)
        for i, nome in enumerate(nomes, start=1):
            print(f"   {i}) {nome} - {projetos[nome].get('title', nome)}")
        try:
            resp = input("\n  Estudo (numero)> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Ate logo!")
            sys.exit(0)
        if resp.isdigit() and 1 <= int(resp) <= len(nomes):
            escolhido = nomes[int(resp) - 1]
        else:
            print("  Opcao invalida. Tente de novo.")
    PROJETO = escolhido
    CONF = projetos[escolhido]
    ENTRADA = CONF.get("input", "dicom")
    try:
        ARQ_PROJETO.write_text(PROJETO + "\n", encoding="utf-8")
    except Exception:
        pass


def cabecalho():
    print("\n" + "=" * 68)
    print(f"  PIPELINE DE NEUROIMAGEM - estudo {PROJETO} (menu guiado)")
    print("=" * 68)
    etapas = CONF.get("steps", {})
    face = "remocao facial -> " if etapas.get("deface", True) else ""
    lesoes = " + LESOES (SAMSEG)" if etapas.get("samseg", False) else ""
    if ENTRADA == "bids":
        print("  Mapa:  BIDS recebido -> importar e desidentificar ->")
    else:
        print("  Mapa:  DICOM -> NIfTI -> desidentificar -> BIDS -> derivados ->")
    print(f"         {face}FreeSurfer -> VOLUMES{lesoes}")
    print("  Dica:  faca os passos em ordem (0,1,2,...). Em caso de duvida,")
    print("         leia o GUIA_DO_ALUNO.pdf ou chame o responsavel.")
    print("=" * 68)


def menu():
    cabecalho()
    if ENTRADA == "bids":
        print(f"\n  Dados recebidos (passo 1): "
              f"{_curto(PROJETOS / PROJETO / 'sourcedata' / 'received')}")
    else:
        print(f"\n  Pasta de dados (passo 1): {_curto(local_dos_dados())}")
        print("  (errada ou em outro lugar? use o item D)")
    print("\n  Escolha um numero/letra e aperte ENTER:\n")
    for num, titulo, _script, _exp, fs in passos_do_projeto():
        marca = "  (precisa do FreeSurfer)" if fs else ""
        print(f"   {num}) {titulo}{marca}")
        if ENTRADA == "bids" and num == "1":
            print("   2-4) (ja incluidos no passo 1 neste estudo)")
    if ENTRADA != "bids":
        print("   D) Onde estao meus arquivos (apontar/conferir a pasta de dados)")
    if len(listar_projetos()) > 1:
        print("   P) Trocar de estudo")
    print("   q) Sair")
    print("")


def rodar_passo(num):
    passo = next((p for p in passos_do_projeto() if p[0].lower() == num), None)
    if passo is None:
        print("  Opcao invalida. Tente de novo.")
        return
    _num, titulo, script, exp, precisa_fs = passo
    caminho = RAIZ / "code" / script
    print("\n" + "-" * 68)
    print(f"  PASSO {num}: {titulo}  (estudo {PROJETO})")
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

    cmd = [sys.executable, str(caminho), "--project", PROJETO] + extra_args

    # O passo 6 (FreeSurfer) roda em lotes: perguntamos quantos em paralelo.
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
            print("\n  ✔ Passo concluido. Veja os resultados na pasta do estudo.")
        else:
            print("\n  ✖ O passo terminou com aviso/erro (codigo "
                  f"{result.returncode}). Leia a mensagem acima; se nao tiver")
            print("    certeza, NAO improvise - chame o responsavel.")
    except KeyboardInterrupt:
        print("\n  (interrompido por voce)")


def main():
    if "--list" in sys.argv:        # modo de teste: mostra o menu de cada estudo e sai
        global PROJETO, ENTRADA, CONF
        for nome, conf in listar_projetos().items():
            PROJETO, CONF, ENTRADA = nome, conf, conf.get("input", "dicom")
            menu()
        return
    escolher_projeto()
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
        if escolha in ("p", "estudo") and len(listar_projetos()) > 1:
            escolher_projeto(perguntar=True)
            continue
        if escolha in ("d", "dados") and ENTRADA != "bids":
            conferir_dados()
        else:
            rodar_passo(escolha)
        input("\n  (aperte ENTER para voltar ao menu) ")


if __name__ == "__main__":
    main()
