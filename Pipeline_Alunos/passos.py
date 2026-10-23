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

O QUE PRECISA DE VOCE: as etapas longas (conversao, BIDS, FreeSurfer, SAMSEG)
rodam sozinhas toda noite no Mac Mini (code/run_batch.py). O seu trabalho e o
que so uma pessoa faz: ler o resumo da ultima rodada (item R), conferir a
qualidade das imagens prontas no freeview (item C, grava aprovado/reprovado em
bids/derivatives/qc/qc_status.tsv) e ensinar ao pipeline as series que ele nao
reconheceu (item S, acrescenta uma regra em projects/<ESTUDO>/series_map.csv).
Os passos 0-8 continuam no menu para rodar a mao quando o responsavel pedir.

Como abrir: de DOIS cliques em "iniciar.command" (na mesma pasta), ou rode
    python3 passos.py
"""

import csv
import json
import os
import re
import shlex
import shutil
import sys
import subprocess
from datetime import date
from pathlib import Path

# A pasta do projeto e a pasta de cima (Pipeline_Alunos fica dentro do projeto).
RAIZ = Path(__file__).resolve().parent.parent
AQUI = Path(__file__).resolve().parent
# PIPELINE_PROJECTS_DIR aponta para outra pasta de estudos (usado nos testes);
# nesse caso o que o menu lembra (estudo, pasta de dados) fica la, nao aqui.
PROJETOS = Path(os.environ.get("PIPELINE_PROJECTS_DIR") or RAIZ / "projects")
ESTADO = PROJETOS if os.environ.get("PIPELINE_PROJECTS_DIR") else AQUI
ARQ_PROJETO = ESTADO / "projeto_atual.txt"

# Ferramentas sem dados que ficam em code/ e o menu tambem usa: o registro de
# QC, as regras de series e a trava do processamento automatico.
sys.path.insert(0, str(RAIZ / "code"))
import batch_lock            # noqa: E402
import qc_status             # noqa: E402
import series_classifier as sc   # noqa: E402

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
    return ESTADO / f"local_dos_dados_{PROJETO}.txt"


_CAMINHOS = {}


def caminhos():
    """Onde fica cada coisa do estudo atual (bids/, logs/batch/, QC...). Quem
    sabe e o code/paths.py (a unica fonte da verdade sobre as pastas); o menu
    so pergunta a ele, uma vez por estudo."""
    if PROJETO not in _CAMINHOS:
        saida = subprocess.run([sys.executable, str(RAIZ / "code" / "paths.py"),
                                "--project", PROJETO],
                               capture_output=True, text=True).stdout
        _CAMINHOS[PROJETO] = {k: Path(v) for k, v in json.loads(saida).items()
                              if k != "PROJECT_NAME"}
    return _CAMINHOS[PROJETO]

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
#  O QUE PRECISA DE VOCE  -  resumo da rodada automatica, QC, series novas
# ===========================================================================

def ler_tsv(caminho, delimiter="\t"):
    """Linhas de uma tabela (lista vazia se o arquivo nao existe)."""
    try:
        with open(caminho, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f, delimiter=delimiter))
    except OSError:
        return []


def batch_rodando():
    """(True, desde) se o processamento automatico esta rodando agora."""
    trava = str(caminhos()["BATCH_LOCK"])
    if batch_lock.is_held(trava):
        return True, batch_lock.holder(trava).get("started", "?")
    return False, None


def series_desconhecidas():
    """Linhas do relatorio de series que nenhuma regra reconheceu."""
    return ler_tsv(caminhos()["CONVERSION_DIR"] / "reports" /
                   "unknown_series_report.csv", delimiter=",")


def qc_pendente():
    c = caminhos()
    return qc_status.pending(str(c["FREESURFER_DIR"]), str(c["SAMSEG_DIR"]),
                             str(c["QC_FILE"]))


ETAPA_PT = {
    "import": "Importar BIDS recebido", "convert": "Converter DICOM (exames novos)",
    "deidentify": "Desidentificar", "bids": "Montar BIDS",
    "derivatives": "Derivados + sourcedata", "deface": "Remocao facial",
    "samseg": "Lesoes (SAMSEG)", "recon": "FreeSurfer (recon)",
    "stats": "Tabelas do FreeSurfer", "batch": "Processamento automatico",
}
SITUACAO_PT = {"ran": "rodou", "skipped": "nada novo para fazer",
               "failed": "FALHOU", "blocked": "nao rodou (um passo anterior falhou)"}
RESULTADO_PT = {
    "OK": "terminou bem",
    "FAILED": "terminou, mas algo FALHOU (veja abaixo)",
    "REFUSED": "NAO comecou (veja o motivo abaixo; e tarefa do responsavel)",
    "STOPPED": "foi interrompida; a proxima rodada continua de onde parou",
}
# O que cada tipo de pendencia quer dizer, e o que fazer.
PENDENCIA_PT = {
    "unknown-series": "Series que o pipeline nao reconheceu -> use o item S",
    "mismatch": "DICOM e JSON com valores diferentes (validation_report.csv) "
                "-> mostre ao responsavel antes de confiar nesse exame",
    "convert-stop": "A conversao parou e precisa de uma decisao do responsavel",
    "import-stop": "A importacao parou e precisa de uma decisao do responsavel",
    "import-check": "Problemas encontrados no BIDS recebido -> avise o responsavel",
    "no-code": "Pasta de exame sem o codigo do sujeito no nome -> o responsavel "
               "renomeia (ex.: GER00039_...)",
    "copy-in-progress": "Pasta ainda sendo copiada -> sera convertida na proxima rodada",
    "gave-up": "Falhou varias vezes e o computador parou de tentar -> chame o responsavel",
    "left-out": "Ficou de fora nesta rodada",
    "qc-failed": "Reprovados no QC -> aguardam decisao do responsavel (refazer?)",
}


def mostrar_resumo():
    """Item R: a ultima rodada automatica, em portugues."""
    arq = caminhos()["BATCH_DIR"] / "last_summary.tsv"
    print("\n" + "-" * 68)
    print(f"  RESUMO DA ULTIMA RODADA AUTOMATICA  (estudo {PROJETO})")
    print("-" * 68)
    rodando, desde = batch_rodando()
    if rodando:
        print(f"  Agora: o processamento automatico esta RODANDO (desde {desde}).")
    linhas = ler_tsv(arq)
    if not linhas:
        print("  Ainda nao houve nenhuma rodada automatica neste estudo.")
        print("  (O responsavel liga com: python3 code/install_schedule.py)")
        return
    info = {r["item"]: r for r in linhas if r["section"] == "run"}
    inicio = info.get("started", {}).get("detail", "?").replace("T", " ")
    fim = info.get("finished", {}).get("detail", "?").replace("T", " ")
    resultado = info.get("result", {}).get("status", "?")
    print(f"  Rodada de {inicio} ate {fim}:")
    print(f"  -> {RESULTADO_PT.get(resultado, resultado)}")
    for r in linhas:
        if r["section"] == "refused":
            print(f"     motivo: {r['detail']}")

    passos = [r for r in linhas if r["section"] == "step"]
    if passos:
        print("\n  O que o computador fez:")
        for r in passos:
            nome = ETAPA_PT.get(r["step"], r["step"])
            extra = f"  ({r['detail']})" if r["status"] in ("ran", "failed") else ""
            print(f"    - {nome}: {SITUACAO_PT.get(r['status'], r['status'])}{extra}")

    falhas = [r for r in linhas if r["section"] == "failed"]
    if falhas:
        print("\n  O que falhou (o computador tenta de novo na proxima rodada;")
        print("  se repetir varias noites, avise o responsavel):")
        for r in falhas:
            print(f"    ✖ {ETAPA_PT.get(r['step'], r['step'])} {r['item']}: {r['detail']}")

    pendencias = [r for r in linhas if r["section"] == "human"]
    print("\n  O que precisa de uma pessoa:")
    if not pendencias:
        print("    nada - otimo!")
    for tipo in dict.fromkeys(r["step"] for r in pendencias):
        print(f"    * {PENDENCIA_PT.get(tipo, tipo)}:")
        for r in [p for p in pendencias if p["step"] == tipo][:10]:
            item = f"{r['item']}: " if r["item"] else ""
            print(f"        {item}{r['detail']}")

    qc = [r for r in linhas if r["section"] == "qc"]
    if qc:
        n_recon = sum(1 for r in qc if r["step"] == "recon")
        n_samseg = sum(1 for r in qc if r["step"] == "samseg")
        print(f"\n  QC para fazer: {n_recon} recon do FreeSurfer, {n_samseg} "
              "mascara(s) do SAMSEG -> use o item C.")
    print(f"\n  (resumo completo, em ingles: {_curto(arq.with_suffix('.md'))})")


KIND_PT = {"recon": "superficies do FreeSurfer", "samseg": "mascara de lesao do SAMSEG"}
COMO_OLHAR = {
    "recon": [
        "Linha AZUL (white) = fronteira substancia branca / cinzenta;",
        "linha VERMELHA (pial) = borda de fora do cortex.",
        "Reprove se: pedaco de cortex fora da linha vermelha, dura-mater ou olho",
        "dentro dela, buraco no cerebro (brainmask cortado), linha azul",
        "atravessando a substancia branca. Passe por varios cortes (setas).",
    ],
    "samseg": [
        "A mascara VERMELHA deve cair nas manchas brancas da FLAIR",
        "(perto dos ventriculos e na substancia branca profunda).",
        "Reprove se a mascara pega a borda dos ventriculos, o cortex ou o",
        "cerebelo, ou se deixa de fora manchas grandes e claras.",
    ],
}


def nota_valida(texto):
    """Sem numeros que parecam data/identificador (6+ digitos, dd/mm/aa)."""
    return not re.search(r"\d{6,}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", texto)


def conferir_qualidade():
    """Item C: abre cada saida pronta no freeview e grava aprovado/reprovado."""
    c = caminhos()
    pendentes = qc_pendente()
    print("\n" + "-" * 68)
    print(f"  CONTROLE DE QUALIDADE (QC)  (estudo {PROJETO})")
    print("-" * 68)
    if not pendentes:
        print("  Nada pendente: tudo o que ficou pronto ja foi conferido.")
        return
    print(f"  {len(pendentes)} item(ns) esperando uma pessoa olhar. Para cada um:")
    print("  o menu abre o freeview; voce olha, FECHA o freeview e diz se")
    print("  aprova (a) ou reprova (r). O registro vai para:")
    print(f"    {_curto(c['QC_FILE'])}")
    tem_freeview = shutil.which("freeview") is not None
    if not tem_freeview:
        print("\n  [!] O freeview nao foi encontrado nesta janela (FreeSurfer nao")
        print("      carregado?). Vou mostrar o comando para voce copiar numa")
        print("      janela com o FreeSurfer carregado.")
    try:
        quem = input("\n  Suas iniciais (para o registro; ENTER pula): ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    vistos = set()
    for n, item in enumerate(pendentes, start=1):
        print("\n" + "." * 68)
        print(f"  [{n}/{len(pendentes)}] {item['subject']} / {item['session']} - "
              f"{KIND_PT[item['kind']]}")
        if item["kind"] == "recon":
            buracos = qc_status.surface_holes(item["folder"])
            if buracos is not None:
                print(f"  Buracos na superficie antes da correcao: {buracos} (compare com")
                print("  os outros exames; bem mais que os colegas = olhe com mais cuidado)")
        else:
            vol = qc_status.lesion_volume(item["folder"])
            if vol is not None:
                print(f"  Volume de lesao calculado: {vol:.0f} mm3")
        if item["kind"] not in vistos:
            vistos.add(item["kind"])
            for linha in COMO_OLHAR[item["kind"]]:
                print(f"    {linha}")
        pasta, cmd = qc_status.freeview_command(item)
        print("  Comando (para abrir a mao):")
        print(f"    cd {shlex.quote(pasta)}")
        print("    " + shlex.join(cmd))
        try:
            resp = input("  ENTER = abrir no freeview, p = pular este, s = sair: ").strip().lower()
            if resp in ("s", "sair"):
                break
            if resp in ("p", "pular"):
                continue
            if tem_freeview:
                print("  (abrindo... feche o freeview para continuar)")
                subprocess.run(cmd, cwd=pasta)
            while True:
                resp = input("  Resultado: a = aprovado, r = reprovado, "
                             "d = decidir depois: ").strip().lower()
                if resp in ("a", "r", "d"):
                    break
                print("  Responda a, r ou d.")
            if resp == "d":
                continue
            while True:
                nota = input("  Observacao (opcional; NUNCA nome de paciente ou "
                             "datas): ").strip()
                if nota_valida(nota):
                    break
                print("  A observacao parece ter uma data ou numero de identificacao;")
                print("  escreva sem isso (ex.: 'pial corta o lobo temporal esquerdo').")
        except (EOFError, KeyboardInterrupt):
            print("\n  (parei; o que ja foi registrado esta salvo)")
            return
        status = "pass" if resp == "a" else "fail"
        qc_status.record(str(c["QC_FILE"]), item["subject"], item["session"],
                         item["kind"], status, nota, quem)
        print(f"  ✔ registrado: {'APROVADO' if status == 'pass' else 'REPROVADO'}")
    print("\n  Fim do QC por agora. Reprovados aparecem no resumo (item R) para o")
    print("  responsavel decidir o que refazer.")


# Sufixos BIDS aceitos por tipo de dado ("" = sem sufixo).
SUFIXOS = {
    "anat": ["T1w", "T2w", "FLAIR", "PDw", "T2starw"],
    "func": ["bold"],
    "dwi": ["dwi"],
    "fmap": ["phasediff", "magnitude", "magnitude1", "magnitude2", "phase1",
             "phase2", "fieldmap", "epi"],
    "perf": ["asl", "m0scan", ""],
    "swi": ["swi"],
    "misc": [""],
}
TIPO_PT = {
    "anat": "estrutural (T1, T2, FLAIR...)",
    "func": "funcional (BOLD / ressonancia funcional)",
    "dwi": "difusao (DWI/DTI)",
    "fmap": "mapa de campo (field map)",
    "perf": "perfusao (ASL)",
    "swi": "suscetibilidade (SWI/SWAN)",
    "misc": "nao e dado de pesquisa (localizador, captura de tela, relatorio)",
}
ENTIDADES = {"task", "acq", "ce", "rec", "dir", "run", "echo", "desc"}
CABECALHO_MAPA = "match_type,pattern,datatype,suffix,entities,derived,notes"


def regras_do_mapa(caminho):
    return sc.load_series_map(str(caminho))


def validar_regra(regra, serie, mapa_estudo):
    """
    Confere uma regra nova ANTES de gravar. `regra` tem as colunas do
    series_map.csv; `serie` e uma linha do relatorio de series desconhecidas.
    Devolve (erros, avisos): listas de textos em portugues.
    """
    erros, avisos = [], []
    tipo, sufixo = regra["datatype"], regra["suffix"]
    if tipo not in SUFIXOS:
        erros.append(f"tipo '{tipo}' invalido (use: {', '.join(SUFIXOS)})")
    elif sufixo not in SUFIXOS[tipo]:
        erros.append(f"sufixo '{sufixo}' nao combina com {tipo} (use: "
                     + ", ".join(s or "(vazio)" for s in SUFIXOS[tipo]) + ")")
    ent = regra["entities"]
    if ent:
        pares = ent.split(";")
        if not all(re.fullmatch(r"[a-z]+=[A-Za-z0-9]+", p) for p in pares):
            erros.append("entidades no formato chave=valor;chave=valor, so letras e "
                         "numeros (ex.: task=rest)")
        elif any(p.split("=")[0] not in ENTIDADES for p in pares):
            erros.append(f"entidade desconhecida (use: {', '.join(sorted(ENTIDADES))})")
    if regra["derived"] not in ("", "yes"):
        erros.append("derivado deve ser 'yes' ou vazio")
    padrao, comparacao = regra["pattern"], regra["match_type"]
    if comparacao not in ("exact", "substring", "regex"):
        erros.append("comparacao deve ser exact, substring ou regex")
    if not padrao.strip():
        erros.append("o padrao esta vazio")
    if padrao.lstrip().startswith("#") or "\n" in padrao or len(padrao) > 120:
        erros.append("o padrao nao pode comecar com # nem ter quebra de linha "
                     "(e no maximo 120 letras)")
    if re.search(r"\d{6,}", padrao):
        erros.append("o padrao tem um numero longo (data? identificador?); este "
                     "arquivo e compartilhado - use so o nome da sequencia")
    if comparacao == "regex":
        try:
            re.compile(padrao)
        except re.error as err:
            erros.append(f"expressao regular invalida: {err}")
    if erros:
        return erros, avisos
    nova = {"match_type": comparacao, "pattern": padrao, "datatype": tipo,
            "suffix": sufixo, "entities": sc._parse_entities(ent),
            "derived": regra["derived"] == "yes", "notes": regra["notes"]}
    descricao = serie["series_description"]
    if sc.classify(descricao, series_map=[nova]).source != "series_map":
        erros.append(f"o padrao nao reconhece o nome da serie ({descricao!r})")
        return erros, avisos
    # A primeira regra que reconhece a serie vence: confere se e mesmo a nova.
    todas = regras_do_mapa(mapa_estudo) + [nova] + sc.load_series_map()
    resultado = sc.classify(descricao, image_type=serie.get("image_type", ""),
                            modality=serie.get("modality", ""),
                            sequence=serie.get("sequence", ""), series_map=todas)
    if resultado.datatype != tipo:
        erros.append(f"outra regra ja existente ganha desta (a serie continuaria "
                     f"como {resultado.datatype}); chame o responsavel")
    if tipo == "func" and "task=" not in ent:
        avisos.append("sem task=... a serie funcional fica em sourcedata/func ate "
                      "alguem definir a tarefa (ex.: task=rest para repouso)")
    if comparacao != "exact":
        avisos.append("um padrao 'contem'/'regex' pode pegar OUTRAS series tambem; "
                      "confira com o responsavel se nao tiver certeza")
    return erros, avisos


def gravar_regra(mapa_estudo, regra, quem=""):
    """Acrescenta a regra (e uma linha de comentario com data/iniciais) ao
    series_map.csv do estudo; confere relendo o arquivo. Devolve True/False."""
    caminho = Path(mapa_estudo)
    antes = len(regras_do_mapa(caminho))
    if not caminho.is_file():
        caminho.write_text(CABECALHO_MAPA + "\n# Series rules of this study (checked "
                           "before code/series_map.csv). First match wins.\n",
                           encoding="utf-8")
    texto = caminho.read_text(encoding="utf-8")
    with open(caminho, "a", newline="", encoding="utf-8") as f:
        if texto and not texto.endswith("\n"):
            f.write("\n")
        f.write(f"# added from the student menu on {date.today().isoformat()}"
                f"{' by ' + quem if quem else ''}\n")
        csv.writer(f, lineterminator="\n").writerow(
            [regra[k] for k in CABECALHO_MAPA.split(",")])
    return len(regras_do_mapa(caminho)) == antes + 1


def perguntar(texto, padrao=""):
    resp = input(f"  {texto}" + (f" [ENTER = {padrao}]" if padrao else "") + ": ").strip()
    return resp or padrao


def revisar_series():
    """Item S: mostra as series desconhecidas e ensina uma regra nova."""
    print("\n" + "-" * 68)
    print(f"  SERIES DESCONHECIDAS  (estudo {PROJETO})")
    print("-" * 68)
    linhas = series_desconhecidas()
    if not linhas:
        print("  Nenhuma: todas as series foram reconhecidas.")
        return
    grupos = {}
    for r in linhas:
        grupos.setdefault(r["series_description"], []).append(r)
    nomes = sorted(grupos)
    print("  Estas series nao bateram com nenhuma regra e ficaram de lado")
    print("  (em bids/sourcedata/unknown/). Escolha uma para ensinar o pipeline:\n")
    for i, nome in enumerate(nomes, start=1):
        r = grupos[nome][0]
        print(f"   {i}) {nome!r}  -  {len(grupos[nome])} exame(s); "
              f"ImageType {r.get('image_type') or '?'}; dica: {r.get('suggestion')}")
    try:
        resp = input("\n  Numero (ENTER volta ao menu): ").strip()
        if not resp.isdigit() or not 1 <= int(resp) <= len(nomes):
            return
        serie = grupos[nomes[int(resp) - 1]][0]
        descricao = serie["series_description"]
        print(f"\n  Serie: {descricao!r}")
        print("  Se nao tiver CERTEZA do que e esta serie, pare aqui e pergunte ao")
        print("  responsavel (ou veja o PDF do protocolo do scanner).\n")
        tipos = list(SUFIXOS)
        for i, t in enumerate(tipos, start=1):
            print(f"   {i}) {t:<5} {TIPO_PT[t]}")
        t = perguntar("Tipo (numero)")
        if not t.isdigit() or not 1 <= int(t) <= len(tipos):
            print("  Opcao invalida; nada foi gravado.")
            return
        tipo = tipos[int(t) - 1]
        opcoes = SUFIXOS[tipo]
        sufixo = opcoes[0]
        if len(opcoes) > 1:
            print("  Sufixo BIDS: " + ", ".join(f"{i}) {s or '(vazio)'}"
                                               for i, s in enumerate(opcoes, start=1)))
            s = perguntar("Sufixo (numero)", "1")
            sufixo = opcoes[int(s) - 1] if s.isdigit() and 1 <= int(s) <= len(opcoes) else "?"
        ent = perguntar("Entidades BIDS (opcional, ex.: task=rest)",
                        "task=rest" if tipo == "func" else "")
        derivado = perguntar("E um mapa calculado pelo scanner (derivado)? s/n", "n")
        print("  Como reconhecer o nome:  1) exatamente este nome (recomendado)  "
              "2) contem um trecho  3) expressao regular (avancado)")
        modo = perguntar("Forma (numero)", "1")
        comparacao = {"1": "exact", "2": "substring", "3": "regex"}.get(modo, "exact")
        padrao = descricao.lower() if comparacao == "exact" else \
            perguntar("Trecho ou expressao (em minusculas)")
        notas = perguntar("Nota (o que e esta serie)", "added from the student menu")
        regra = {"match_type": comparacao, "pattern": padrao, "datatype": tipo,
                 "suffix": sufixo, "entities": ent,
                 "derived": "yes" if derivado.lower() in ("s", "sim", "y") else "",
                 "notes": notas}
        mapa = caminhos()["PROJECT_SERIES_MAP"]
        erros, avisos = validar_regra(regra, serie, mapa)
        if erros:
            print("\n  [x] A regra NAO foi gravada:")
            for e in erros:
                print(f"      - {e}")
            return
        for a in avisos:
            print(f"  [!] {a}")
        print("\n  Linha que vai para " + _curto(mapa) + ":")
        print("    " + ",".join(regra[k] for k in CABECALHO_MAPA.split(",")))
        if perguntar("Gravar? s/n", "n").lower() not in ("s", "sim", "y"):
            print("  Ok, nada foi gravado.")
            return
        quem = perguntar("Suas iniciais (vao num comentario do arquivo)")
    except (EOFError, KeyboardInterrupt):
        print("\n  (cancelado; nada foi gravado)")
        return
    if gravar_regra(mapa, regra, quem):
        print(f"  ✔ Regra gravada. Na proxima rodada automatica os exames com esta")
        print(f"    serie sao convertidos de novo e ela entra como {tipo}.")
    else:
        print("  [x] Algo deu errado ao reler o arquivo; chame o responsavel.")


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
    print("  Os passos longos rodam sozinhos toda noite no Mac Mini. O SEU")
    print("  trabalho e o que precisa de uma pessoa: itens R, C e S abaixo.")
    print("  Em caso de duvida, leia o GUIA_DO_ALUNO.pdf ou chame o responsavel.")
    print("=" * 68)
    situacao()


def situacao():
    """Uma linha sobre o processamento automatico e as pendencias."""
    try:
        rodando, desde = batch_rodando()
        linhas = ler_tsv(caminhos()["BATCH_DIR"] / "last_summary.tsv")
        n_qc, n_series = len(qc_pendente()), len(series_desconhecidas())
    except Exception:
        return
    if rodando:
        print(f"  Processamento automatico: RODANDO agora (desde {desde}).")
    elif linhas:
        info = {r["item"]: r for r in linhas if r["section"] == "run"}
        quando = info.get("finished", {}).get("detail", "?").replace("T", " ")[:16]
        resultado = info.get("result", {}).get("status", "?")
        print(f"  Ultima rodada automatica: {quando} - "
              f"{RESULTADO_PT.get(resultado, resultado)}")
    else:
        print("  Processamento automatico: ainda nao rodou neste estudo.")
    print(f"  Pendente para voce: {n_qc} QC, {n_series} serie(s) desconhecida(s).")


def menu():
    cabecalho()
    if ENTRADA == "bids":
        print(f"\n  Dados recebidos (passo 1): "
              f"{_curto(PROJETOS / PROJETO / 'sourcedata' / 'received')}")
    else:
        print(f"\n  Pasta de dados (passo 1): {_curto(local_dos_dados())}")
        print("  (errada ou em outro lugar? use o item D)")
    print("\n  Escolha um numero/letra e aperte ENTER:\n")
    print("  O QUE PRECISA DE VOCE:")
    print("   R) Resumo da ultima rodada automatica (o que rodou, o que falhou)")
    print("   C) Conferir a qualidade (QC) dos exames prontos, no freeview")
    print("   S) Series desconhecidas: revisar e ensinar uma regra")
    print("\n  PASSOS (rodar a mao so quando o responsavel pedir):")
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
    rodando, desde = batch_rodando()
    if rodando and script != "check_environment.py":
        print(f"\n  O processamento automatico esta rodando agora (desde {desde}).")
        print("  Nao rode passos a mao ao mesmo tempo (eles mexeriam nas mesmas")
        print("  pastas). Use o item R para acompanhar, ou espere ele terminar.")
        return
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
        elif escolha in ("r", "resumo"):
            mostrar_resumo()
        elif escolha in ("c", "qc"):
            conferir_qualidade()
        elif escolha in ("s", "series"):
            revisar_series()
        else:
            rodar_passo(escolha)
        input("\n  (aperte ENTER para voltar ao menu) ")


if __name__ == "__main__":
    main()
