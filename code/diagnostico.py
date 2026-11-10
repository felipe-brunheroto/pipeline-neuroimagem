"""
diagnostico.py - o computador esta pronto para o pipeline?
==========================================================

Confere, explica em portugues simples e (se voce quiser) corrige o ambiente
do pipeline neste Mac: Python, dcm2niix, FreeSurfer e licenca, espaco em
disco, criptografia do disco, o processamento automatico da noite, a ultima
rodada e se o codigo esta atualizado.

    python3 code/diagnostico.py                 # confere e explica
    python3 code/diagnostico.py --salvar        # + grava o relatorio em logs/diagnostico/
    python3 code/diagnostico.py --sem-perguntas # nao oferece correcoes (uso em scripts)
    python3 code/diagnostico.py --offline       # nao consulta o GitHub

Ou dois cliques em Pipeline_Alunos/diagnostico.command, ou o item 0 do menu.

O QUE ELE NUNCA FAZ
  * Nao le nem mostra dado de paciente: dos estudos so CONTA pastas. Nome de
    pasta de exame nunca aparece (no COGAGING ele pode conter nome de paciente).
  * Nao usa sudo. O que precisa de senha de administrador (criptografia do
    disco, acordar o Mac de noite, acesso total ao disco) ele explica, e uma
    pessoa faz.
  * So corrige com o seu "s", item por item, e so o que e seguro: preparar o
    Python do projeto, copiar a licenca do FreeSurfer e (re)instalar o
    processamento da noite.

Codigo de saida: 0 = nada FALTA (pode haver ATENCAO), 1 = algo FALTA.
Biblioteca padrao apenas: roda antes de qualquer instalacao.
"""

import argparse
import glob
import os
import plistlib
import re
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

CODE = Path(__file__).resolve().parent
REPO = CODE.parent
PROJECTS = Path(os.environ.get("PIPELINE_PROJECTS_DIR") or REPO / "projects")
INBOX = PROJECTS.parent / "inbox"
VENV_PY = REPO / ".venv" / "bin" / "python"
AGENTS = Path.home() / "Library" / "LaunchAgents"
PREFIX = "org.pipeline-neuroimagem."
HORA_PADRAO = "22:30"
MIN_GB = 50          # o mesmo minimo do run_batch.py (--min-free-gb)
GB = 1024 ** 3

sys.path.insert(0, str(CODE))
import batch_lock  # noqa: E402  (biblioteca padrao, sem import de projeto)

OK, ATENCAO, FALTA, INFO = "OK", "ATENCAO", "FALTA", "INFO"
MARCA = {OK: "[  OK  ]", ATENCAO: "[ATENCAO]", FALTA: "[ FALTA]", INFO: "[ info ]"}


class Item:
    """Uma conferencia: o que foi visto, o que significa e como resolver."""

    def __init__(self, area, titulo, estado, visto, explica="", resolver="",
                 corrigir=None, correcao=""):
        self.area, self.titulo, self.estado, self.visto = area, titulo, estado, visto
        self.explica, self.resolver = explica, resolver
        self.corrigir, self.correcao = corrigir, correcao


def roda(cmd, timeout=20, **kw):
    """(codigo, saida) de um comando; (None, '') se ele nem existe."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return None, ""


def sysctl(chave):
    _, s = roda(["sysctl", "-n", chave], timeout=5)
    return int(s) if s.isdigit() else None


def pergunta(texto):
    try:
        return input(f"  {texto} (s/n): ").strip().lower() in ("s", "sim", "y", "yes")
    except EOFError:
        return False


# ---------------------------------------------------------------------------
#  FreeSurfer: onde esta (a mesma regra do start.command)
# ---------------------------------------------------------------------------

def freesurfer_home():
    env = os.environ.get("FREESURFER_HOME", "")
    if env and os.path.isfile(os.path.join(env, "SetUpFreeSurfer.sh")):
        return env
    casas = [os.path.dirname(p) for p in glob.glob("/Applications/freesurfer/*/SetUpFreeSurfer.sh")]
    chave = lambda h: [int(x) for x in re.findall(r"\d+", os.path.basename(h))] or [0]
    return max(casas, key=chave) if casas else ""


def licenca(home):
    for c in (os.environ.get("FS_LICENSE"), os.path.join(home, "license.txt"),
              os.path.join(home, ".license")):
        if c and os.path.isfile(c) and os.path.getsize(c) > 0:
            return c
    return None


# ---------------------------------------------------------------------------
#  Correcoes seguras (so com o "s" da pessoa)
# ---------------------------------------------------------------------------

def python_novo():
    for cand in ("python3.13", "python3.12", "python3.11", "python3.10",
                 "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "python3"):
        exe = shutil.which(cand) or (cand if os.path.isfile(cand) else None)
        if exe and roda([exe, "-c", "import sys; sys.exit(sys.version_info < (3, 10))"])[0] == 0:
            return exe
    return None


def corrigir_python():
    exe = python_novo()
    if not exe:
        print("  Nao achei Python 3.10 ou mais novo. Instale pelo site")
        print("  https://www.python.org/downloads/macos/ e rode o diagnostico de novo.")
        return False
    if not VENV_PY.exists():
        print(f"  Criando a pasta .venv/ com {exe} ...")
        if subprocess.run([exe, "-m", "venv", str(REPO / ".venv")]).returncode:
            return False
    print("  Instalando os pacotes de requirements.txt (pode levar 1-2 minutos) ...")
    ok = subprocess.run([str(VENV_PY), "-m", "pip", "install", "-q", "--upgrade", "pip"]).returncode == 0 \
        and subprocess.run([str(VENV_PY), "-m", "pip", "install", "-q", "-r",
                            str(REPO / "requirements.txt"), "pytest"]).returncode == 0
    if ok:
        shutil.copyfile(REPO / "requirements.txt", REPO / ".venv" / "requirements.installed")
    return ok


def corrigir_licenca(home):
    def acao():
        print("  Arraste o arquivo license.txt (o que chegou por e-mail do FreeSurfer)")
        print("  para esta janela e aperte ENTER:")
        try:
            bruto = input("  licenca> ").strip().strip("'\"").replace("\\ ", " ")
        except EOFError:
            return False
        origem = Path(bruto).expanduser()
        if not origem.is_file() or origem.stat().st_size == 0:
            print("  Esse arquivo nao existe ou esta vazio. Nada foi copiado.")
            return False
        destino = Path(home) / "license.txt"
        try:
            shutil.copyfile(origem, destino)
        except PermissionError:
            print(f"  Sem permissao para gravar em {home}. Peca a quem administra o Mac:")
            print(f"      sudo cp {origem} {destino}")
            return False
        print(f"  Licenca copiada para {destino}.")
        return True
    return acao


def limite_agendado():
    """O --jobs gravado no agendamento atual (0 = automatico), para nao perde-lo ao reinstalar."""
    try:
        with (AGENTS / (PREFIX + "nightly.plist")).open("rb") as f:
            texto = " ".join(plistlib.load(f).get("ProgramArguments", []))
    except Exception:
        return 0
    m = re.search(r"--jobs['\"]? ['\"]?(\d+)", texto)
    return int(m.group(1)) if m else 0


def corrigir_agendamento(home):
    def acao():
        if not VENV_PY.exists():
            # O agendamento grava o Python que o roda: sem .venv seria o do sistema, sem pacotes.
            print("  Primeiro prepare o Python do projeto (item 'Python do projeto' acima).")
            return False
        py = str(VENV_PY)
        env = dict(os.environ)
        if home:
            env["FREESURFER_HOME"] = home
        limite = limite_agendado()
        extra = ["--jobs", str(limite)] if limite else []
        return subprocess.run([py, str(CODE / "pipeline.py"), "schedule", "--at", HORA_PADRAO, *extra],
                              cwd=str(REPO), env=env).returncode == 0
    return acao


# ---------------------------------------------------------------------------
#  As conferencias
# ---------------------------------------------------------------------------

def computador():
    A = "Computador"
    itens = []
    apple = sysctl("hw.optional.arm64") == 1
    itens.append(Item(A, "Processador", OK if apple else ATENCAO,
                      "Apple Silicon (M1/M2/M3/M4)" if apple else "Intel",
                      "" if apple else "O SAMSEG (lesoes, COGAGING) so roda com FreeSurfer nativo de "
                      "Apple Silicon; num Mac Intel ele aborta.",
                      "" if apple else "Rode o processamento pesado num Mac com chip M."))

    nucleos = sysctl("hw.perflevel0.physicalcpu") or os.cpu_count() or 4
    ram = (sysctl("hw.memsize") or 16 * GB) / GB
    jobs = max(1, min(nucleos // 2, int((ram - 8) // 8)))
    itens.append(Item(A, "Memoria e nucleos", OK if ram >= 32 else ATENCAO,
                      f"{ram:.0f} GB de memoria, {nucleos} nucleos de desempenho: cabem ~{jobs} "
                      "FreeSurfer ao mesmo tempo",
                      "" if ram >= 32 else "Cada FreeSurfer usa ~8 GB. Com pouca memoria a fila da "
                      "noite anda devagar (funciona, mas leva mais noites).",
                      "" if ram >= 32 else "Nada a fazer aqui; o pipeline ja se ajusta a memoria."))

    livre = shutil.disk_usage(PROJECTS if PROJECTS.exists() else REPO).free / GB
    estado = FALTA if livre < MIN_GB else ATENCAO if livre < 3 * MIN_GB else OK
    itens.append(Item(A, "Espaco livre no disco", estado, f"{livre:.0f} GB livres",
                      f"O processamento da noite se RECUSA a comecar com menos de {MIN_GB} GB "
                      "livres (cada CD ~1 GB, cada FreeSurfer ~1 GB)." if estado != OK else "",
                      "Libere espaco (Ajustes > Geral > Armazenamento) ou mova dados antigos "
                      "para um disco de backup aprovado." if estado != OK else ""))

    cod, s = roda(["fdesetup", "status"], timeout=10)
    if cod is None:
        itens.append(Item(A, "Criptografia do disco (FileVault)", INFO, "nao deu para verificar"))
    else:
        ligado = "is On" in s
        itens.append(Item(A, "Criptografia do disco (FileVault)", OK if ligado else FALTA,
                          "ligado" if ligado else "DESLIGADO",
                          "" if ligado else "Este Mac guarda DICOM com nome e data de nascimento de "
                          "pacientes. Sem FileVault, quem levar o disco le tudo.",
                          "" if ligado else "Ajustes do Sistema > Privacidade e Seguranca > FileVault > "
                          "Ativar (precisa da senha de administrador; guarde a chave de recuperacao "
                          "num lugar seguro, fora deste Mac)."))

    casa = Path.home()
    protegidas = [casa / "Desktop", casa / "Documents", casa / "Downloads",
                  casa / "Library" / "Mobile Documents", Path("/Volumes")]
    dentro = next((p for p in protegidas if REPO == p or p in REPO.parents), None)
    itens.append(Item(A, "Pasta do pipeline", ATENCAO if dentro else OK, str(REPO),
                      f"A pasta esta dentro de {dentro}. O macOS protege essa pasta e pode BLOQUEAR "
                      "o processamento da noite em silencio ('Operation not permitted' no log)."
                      if dentro else "",
                      "O mais simples: clonar o pipeline em ~/pipeline-neuroimagem (veja o "
                      "QUICKSTART). Ou: Ajustes > Privacidade e Seguranca > Acesso Total ao Disco > "
                      "adicionar /bin/bash." if dentro else ""))
    return itens


def programas():
    A = "Programas"
    itens = []
    if not VENV_PY.exists():
        itens.append(Item(A, "Python do projeto (.venv)", FALTA, "a pasta .venv/ nao existe",
                          "O pipeline usa um Python proprio, na pasta .venv/, com as versoes certas "
                          "do pydicom e do dcm2niix.",
                          "Rode o start.command uma vez (ele prepara tudo), ou aceite a correcao abaixo.",
                          corrigir_python, "criar .venv/ e instalar os pacotes"))
    else:
        _, ver = roda([str(VENV_PY), "--version"])
        sonda = ("import os, sys, pydicom, dcm2niix; "
                 "sys.exit(0 if sys.version_info >= (3, 10) and os.path.exists(dcm2niix.bin) else 3)")
        cod, _ = roda([str(VENV_PY), "-c", sonda], timeout=60)
        marca = REPO / ".venv" / "requirements.installed"
        em_dia = marca.is_file() and marca.read_bytes() == (REPO / "requirements.txt").read_bytes()
        if cod == 0 and em_dia:
            itens.append(Item(A, "Python, pydicom e dcm2niix", OK, f"{ver} em .venv/"))
        else:
            itens.append(Item(A, "Python, pydicom e dcm2niix", FALTA,
                              f"{ver or 'Python'} em .venv/, mas os pacotes "
                              f"{'faltam ou nao abrem' if cod else 'estao em versao antiga'}",
                              "Sem pydicom e dcm2niix nenhum DICOM e convertido.",
                              "Aceite a correcao abaixo (ou rode o start.command).",
                              corrigir_python, "reinstalar os pacotes do requirements.txt"))

    home = freesurfer_home()
    if not home:
        itens.append(Item(A, "FreeSurfer", FALTA, "nao encontrado em /Applications/freesurfer",
                          "Sem FreeSurfer o pipeline so converte e organiza (DICOM -> BIDS); nao "
                          "mede volumes nem lesoes.",
                          "Instale o FreeSurfer 8 para macOS Apple Silicon (arm64): "
                          "https://surfer.nmr.mgh.harvard.edu/fswiki/DownloadAndInstall"))
        return itens
    stamp = ""
    try:
        stamp = Path(home, "build-stamp.txt").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    itens.append(Item(A, "FreeSurfer", OK, f"{home}" + (f"  ({stamp})" if stamp else "")))

    _, tipo = roda(["file", "-b", os.path.join(home, "bin", "mri_coreg")], timeout=10)
    if sysctl("hw.optional.arm64") == 1 and "x86_64" in tipo and "arm64" not in tipo:
        itens.append(Item(A, "FreeSurfer nativo", FALTA, "versao Intel rodando via Rosetta",
                          "Nessa versao o SAMSEG aborta e o recon-all fica varias vezes mais lento.",
                          "Instale o FreeSurfer 8 para Apple Silicon (arm64) no lugar deste."))
    lic = licenca(home)
    itens.append(Item(A, "Licenca do FreeSurfer", OK if lic else FALTA,
                      lic or f"nao ha license.txt em {home}",
                      "" if lic else "Sem licenca o FreeSurfer nao roda, e o processamento da noite "
                      "se recusa a comecar. A licenca e gratuita e chega por e-mail.",
                      "" if lic else "Peca em https://surfer.nmr.mgh.harvard.edu/registration.html "
                      "e aceite a correcao abaixo para copia-la para o lugar certo.",
                      corrigir_licenca(home) if not lic else None, "copiar o license.txt"))
    return itens


def automatico():
    A = "Processamento automatico (noite)"
    itens = []
    home = freesurfer_home()
    jobs = sorted(AGENTS.glob(PREFIX + "*.plist")) if AGENTS.is_dir() else []
    noturno = AGENTS / (PREFIX + "nightly.plist")
    if not jobs:
        itens.append(Item(A, "Agendamento", FALTA, "nenhum processamento agendado neste Mac",
                          "Sem agendamento ninguem processa os exames novos: eles ficam parados em "
                          "inbox/ ou em Patient Files/ ate alguem rodar a mao.",
                          f"Aceite a correcao abaixo (agenda todo dia as {HORA_PADRAO}).",
                          corrigir_agendamento(home), f"agendar todo dia as {HORA_PADRAO}"))
    else:
        for p in jobs:
            try:
                with p.open("rb") as f:
                    job = plistlib.load(f)
            except Exception:
                itens.append(Item(A, f"Agendamento {p.stem}", ATENCAO, "arquivo ilegivel", "",
                                  "Reinstale com a correcao abaixo.", corrigir_agendamento(home),
                                  "reinstalar o agendamento"))
                continue
            texto = " ".join(job.get("ProgramArguments", []))
            hora = job.get("StartCalendarInterval", {})
            quando = f"{hora.get('Hour', 0):02d}:{hora.get('Minute', 0):02d}" if hora else "?"
            j = re.search(r"--jobs['\"]? ['\"]?(\d+)", texto)
            limite = (f", ate {j.group(1)} FreeSurfer ao mesmo tempo" if j
                      else ", FreeSurfer: quantos couberem")
            m = re.search(r"cd ('[^']*'|\S+)", texto)
            pasta = m.group(1).strip("'").rstrip(";") if m else ""
            carregado = roda(["launchctl", "print", f"gui/{os.getuid()}/{job.get('Label', p.stem)}"],
                             timeout=10)[0] == 0
            problemas = []
            if pasta and Path(pasta).resolve() != REPO:
                problemas.append(f"aponta para OUTRA copia do pipeline ({pasta})")
            if home and "FREESURFER_HOME" in texto and home not in texto:
                problemas.append("usa um FreeSurfer diferente do instalado agora")
            if not carregado:
                problemas.append("esta instalado mas nao carregado no macOS")
            if p != noturno:
                problemas.append("e o agendamento antigo, por estudo (o atual cobre todos)")
            itens.append(Item(A, "Agendamento", ATENCAO if problemas else OK,
                              f"todo dia as {quando}{limite}"
                              + ("; " + "; ".join(problemas) if problemas else ""),
                              "O processamento da noite precisa rodar ESTA copia do pipeline, com o "
                              "FreeSurfer instalado." if problemas else "",
                              "Aceite a correcao abaixo: ela reinstala e remove os antigos."
                              if problemas else "",
                              corrigir_agendamento(home) if problemas else None,
                              "reinstalar o agendamento"))

        cod, s = roda(["pmset", "-g", "sched"], timeout=10)
        acorda = cod == 0 and re.search(r"wake(orpoweron|poweron)?", s or "")
        itens.append(Item(A, "Acordar o Mac para rodar", OK if acorda else ATENCAO,
                          "o Mac acorda sozinho" if acorda else "o Mac NAO esta programado para acordar",
                          "" if acorda else "Se o Mac estiver dormindo no horario, o processamento so "
                          "comeca quando alguem acordar o Mac.",
                          "" if acorda else "Uma pessoa com senha de administrador roda no Terminal:\n"
                          "sudo pmset repeat wakeorpoweron MTWRFSU 22:25:00"))

    log = REPO / "logs" / "nightly.log"
    if log.is_file():
        fim = log.read_text(encoding="utf-8", errors="replace")[-20000:]
        if "Operation not permitted" in fim:
            itens.append(Item(A, "Bloqueio de privacidade do macOS", FALTA,
                              "o log da noite mostra 'Operation not permitted'",
                              "O macOS impediu o processamento automatico de abrir a pasta do pipeline.",
                              "Ajustes > Privacidade e Seguranca > Acesso Total ao Disco > adicionar "
                              "/bin/bash, ou mover o pipeline para ~/pipeline-neuroimagem."))

    trava = PROJECTS / ".batch.lock"
    if batch_lock.is_held(str(trava)):
        desde = batch_lock.holder(str(trava)).get("started", "?")
        itens.append(Item(A, "Agora", INFO, f"processamento RODANDO desde {desde}"))
    for conf in sorted(PROJECTS.glob("*/project.json")):
        resumo = conf.parent / "logs" / "batch" / "last_summary.tsv"
        nome = conf.parent.name
        if not resumo.is_file():
            itens.append(Item(A, f"Ultima rodada - {nome}", INFO, "ainda nao rodou neste estudo"))
            continue
        info = {}
        for linha in resumo.read_text(encoding="utf-8").splitlines()[1:]:
            c = linha.split("\t")
            if len(c) >= 5 and c[0] == "run":
                info[c[2]] = c[3] or c[4]
        fim_em = info.get("finished", "")
        res = info.get("result", "?")
        try:
            dias = (datetime.now() - datetime.fromisoformat(fim_em)).days
        except ValueError:
            dias = None
        texto = {"OK": "terminou bem", "FAILED": "terminou com FALHA", "REFUSED": "NAO comecou",
                 "STOPPED": "foi interrompida"}.get(res, res)
        velho = jobs and dias is not None and dias >= 2
        estado = ATENCAO if res in ("FAILED", "REFUSED") or velho else OK
        itens.append(Item(A, f"Ultima rodada - {nome}", estado,
                          f"{fim_em.replace('T', ' ')[:16]}: {texto}"
                          + (f" (ha {dias} dias)" if velho else ""),
                          "A rodada da noite deixou um resumo do que aconteceu." if estado != OK else "",
                          f"Leia projects/{nome}/logs/batch/last_summary.md (ou o item R do menu)."
                          if estado != OK else ""))
    return itens


def dados():
    A = "Estudos (so contagens)"
    itens = []
    confs = sorted(PROJECTS.glob("*/project.json"))
    if not confs:
        return [Item(A, "Estudos", FALTA, f"nenhum project.json em {PROJECTS}",
                     "Sem estudo configurado o pipeline nao sabe para onde vai cada exame.",
                     "Confira se esta e a pasta certa do pipeline (deve ter projects/CBR e "
                     "projects/COGAGING).")]
    for conf in confs:
        raiz = conf.parent
        pf = raiz / "Patient Files"
        n_ex = sum(1 for p in pf.iterdir() if p.is_dir()) if pf.is_dir() else 0
        bids = raiz / "bids"
        n_sub = sum(1 for p in bids.glob("sub-*") if p.is_dir()) if bids.is_dir() else 0
        itens.append(Item(A, raiz.name, INFO,
                          f"{n_ex} exame(s) em Patient Files, {n_sub} participante(s) em bids/"))
    if INBOX.is_dir():
        n = sum(1 for p in INBOX.iterdir() if not p.name.startswith((".", "LEIA-ME")))
        itens.append(Item(A, "inbox/", INFO,
                          f"{n} item(ns) esperando a proxima rodada" if n else "vazia"))
    return itens


def codigo(offline):
    A = "Codigo do pipeline"
    if not (REPO / ".git").exists():
        return [Item(A, "Git", INFO, "esta pasta nao e um clone do git (nao da para conferir)")]
    itens = []
    _, mod = roda(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"])
    if mod:
        n = len(mod.splitlines())
        itens.append(Item(A, "Alteracoes locais", ATENCAO, f"{n} arquivo(s) de codigo mudados neste Mac",
                          "Codigo editado direto na maquina de processamento nao tem backup e "
                          "atrapalha a atualizacao.",
                          "Avise o responsavel; ele decide se vira commit ou se desfaz."))
    if offline:
        return itens
    cod, _ = roda(["git", "-C", str(REPO), "fetch", "-q"], timeout=30)
    if cod != 0:
        itens.append(Item(A, "Atualizacoes", INFO, "sem acesso ao GitHub agora (sem internet?)"))
        return itens
    _, n = roda(["git", "-C", str(REPO), "rev-list", "--count", "HEAD..@{u}"])
    novos = int(n) if n.isdigit() else 0
    itens.append(Item(A, "Atualizacoes", ATENCAO if novos else OK,
                      f"{novos} mudanca(s) nova(s) no GitHub" if novos else "em dia com o GitHub",
                      "Ha codigo novo que ainda nao esta neste Mac." if novos else "",
                      "Rode o start.command: ele mostra o que mudou e so atualiza com o seu 'y'."
                      if novos else ""))
    return itens


# ---------------------------------------------------------------------------
#  Saida
# ---------------------------------------------------------------------------

def relatorio(itens):
    nome = roda(["scutil", "--get", "ComputerName"], timeout=5)[1] or os.uname().nodename
    linhas = ["=" * 70,
              "  DIAGNOSTICO DO COMPUTADOR - pipeline de neuroimagem",
              f"  {nome} - {datetime.now():%d/%m/%Y %H:%M}",
              "=" * 70]
    area = None
    for it in itens:
        if it.area != area:
            area = it.area
            linhas += ["", f"  {area.upper()}"]
        linhas.append(f"  {MARCA[it.estado]} {it.titulo}: {it.visto}")
        if it.estado in (FALTA, ATENCAO):
            for rotulo, txt in (("Por que importa: ", it.explica), ("Como resolver:   ", it.resolver)):
                for par in filter(None, txt.split("\n")):
                    linhas += textwrap.wrap(par, 78, initial_indent=" " * 11 + rotulo,
                                            subsequent_indent=" " * 28,
                                            break_long_words=False, break_on_hyphens=False)
                    rotulo = " " * len(rotulo)
    faltam = [i for i in itens if i.estado == FALTA]
    atencao = [i for i in itens if i.estado == ATENCAO]
    linhas += ["", "-" * 70]
    if not faltam and not atencao:
        linhas.append("  RESULTADO: tudo pronto. Este Mac pode processar sozinho.")
    else:
        linhas.append(f"  RESULTADO: {len(faltam)} item(ns) FALTA, {len(atencao)} pedem ATENCAO.")
        linhas.append("  Comece pelos que FALTAM, de cima para baixo. Na duvida, NAO improvise:")
        linhas.append("  salve este relatorio (--salvar) e mande para o responsavel.")
    linhas.append("-" * 70)
    return "\n".join(linhas)


def main():
    ap = argparse.ArgumentParser(description="Confere e explica o ambiente do pipeline neste Mac.")
    ap.add_argument("--salvar", action="store_true", help="grava o relatorio em logs/diagnostico/")
    ap.add_argument("--sem-perguntas", action="store_true", help="nao oferece correcoes")
    ap.add_argument("--offline", action="store_true", help="nao consulta o GitHub")
    ap.add_argument("--project", help=argparse.SUPPRESS)   # o menu passa; aqui vale para todos
    args = ap.parse_args()

    print("  Conferindo o computador (leva alguns segundos)...")
    itens = computador() + programas() + automatico() + dados() + codigo(args.offline)
    texto = relatorio(itens)
    print(texto)

    if args.salvar:
        pasta = REPO / "logs" / "diagnostico"
        pasta.mkdir(parents=True, exist_ok=True)
        arq = pasta / f"diagnostico_{datetime.now():%Y-%m-%d_%H%M}.txt"
        arq.write_text(texto + "\n", encoding="utf-8")
        print(f"  Relatorio salvo em {arq}")
        print("  (Ele nao tem dado de paciente: so contagens e configuracoes.)")

    corrigiveis = [i for i in itens if i.corrigir and i.estado in (FALTA, ATENCAO)]
    if corrigiveis and not args.sem_perguntas and sys.stdin.isatty():
        print("\n  Posso corrigir agora, um de cada vez:")
        for i in corrigiveis:
            print(f"\n  -> {i.titulo}: {i.correcao}")
            if pergunta("Fazer isso agora?"):
                print("  Feito." if i.corrigir() else "  Nao deu certo; veja a mensagem acima.")
            else:
                print("  Ok, nada foi feito.")
        print("\n  Rode o diagnostico de novo para conferir o resultado.")
    return 1 if any(i.estado == FALTA for i in itens) else 0


if __name__ == "__main__":
    sys.exit(main())
