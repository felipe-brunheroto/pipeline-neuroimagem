"""
make_student_guide.py
=====================

Generates GUIA_DO_ALUNO.pdf — a Brazilian-Portuguese, non-programmer-friendly,
step-by-step guide for students running the CBR pipeline on the Recon Mac.
Self-contained (only needs reportlab). Contains NO patient data.

Run (on a machine with reportlab):  python make_student_guide.py
"""

from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable, ListFlowable, ListItem)

ACCENT = colors.HexColor("#1f4e79")
LIGHT = colors.HexColor("#eaf0f6")
GREEN = colors.HexColor("#2e7d32")
GREENBG = colors.HexColor("#eaf6ec")
WARN = colors.HexColor("#fff4e5")
GREY = colors.HexColor("#555555")


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;")


def styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=ss["Title"], textColor=ACCENT,
                                fontSize=21, leading=25, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=ss["Normal"], textColor=GREY,
                              fontSize=11, leading=14),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], textColor=ACCENT,
                             fontSize=13.5, leading=16, spaceBefore=13, spaceAfter=5),
        "body": ParagraphStyle("b", parent=ss["BodyText"], fontSize=10.2,
                               leading=14.5, spaceAfter=6),
        "small": ParagraphStyle("sm", parent=ss["Normal"], fontSize=8.5,
                                leading=11, textColor=GREY),
        "rule": ParagraphStyle("r", parent=ss["BodyText"], fontSize=10, leading=13.5,
                               backColor=GREENBG, borderColor=GREEN, borderWidth=0.8,
                               borderPadding=8, spaceAfter=6),
        "warn": ParagraphStyle("w", parent=ss["BodyText"], fontSize=10, leading=13.5,
                               backColor=WARN, borderColor=colors.HexColor("#e0a030"),
                               borderWidth=0.8, borderPadding=8, spaceAfter=6),
        "cellh": ParagraphStyle("ch", parent=ss["Normal"], fontSize=9,
                                textColor=colors.white, fontName="Helvetica-Bold"),
        "cell": ParagraphStyle("c", parent=ss["Normal"], fontSize=8.8, leading=11.5),
    }


def bullets(items, st, style="body"):
    return ListFlowable(
        [ListItem(Paragraph(esc(t), st[style]), leftIndent=10) for t in items],
        bulletType="bullet", start="circle", leftIndent=12)


def steps_table(st):
    rows = [
        ["Nº", "O que você manda fazer", "O que acontece / onde fica o resultado"],
        ["0", "Checar o ambiente",
         "Confere se as ferramentas estão instaladas. Não muda nada."],
        ["1", "Converter DICOM em NIfTI",
         "Cria as imagens .nii.gz organizadas em BIDS_output/, com provenance.csv "
         "e validation_report.csv. Os DICOM originais não são tocados."],
        ["2", "Desidentificar",
         "Remove identificadores dos arquivos .json. Só depois disto os dados "
         "estão desidentificados."],
        ["3", "Remoção facial (defacing)",
         "Cria cópias sem rosto em BIDS_output/derivatives/defaced/. "
         "Mantém os originais. Precisa do FreeSurfer."],
        ["4", "Montar o BIDS",
         "Gera a pasta bids/ (formato padrão FAIR), só com as anatômicas."],
        ["5", "FreeSurfer (recon)",
         "Reconstrói o cérebro e compara cada pessoa no tempo. MUITO demorado — "
         "roda em lotes (você escolhe quantos por vez). Precisa do FreeSurfer."],
        ["6", "Extrair os volumes",
         "Gera tabelas (.tsv) de volume e espessura por região, em "
         "derivatives/fs_tables/. É o produto final desta etapa."],
    ]
    data = [[Paragraph(esc(c), st["cellh"] if i == 0 else st["cell"])
             for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=[9*mm, 52*mm, 109*mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c5d2e0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def build(path):
    st = styles()
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
                            topMargin=15*mm, bottomMargin=15*mm,
                            title="Guia do Aluno - pipeline CBR")
    F = []
    F.append(Paragraph("Guia do Aluno — do DICOM aos volumes do cérebro", st["title"]))
    F.append(Paragraph("Pipeline CBR · FreeSurfer 8 · passo a passo para quem está "
                       "abrindo o Terminal pela primeira vez", st["sub"]))
    F.append(Spacer(1, 3))
    F.append(Paragraph(f"Atualizado em {date.today().isoformat()} &nbsp;·&nbsp; "
                       "em português &nbsp;·&nbsp; <b>não contém dados de "
                       "pacientes</b>", st["small"]))
    F.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceBefore=8,
                        spaceAfter=8))

    F.append(Paragraph("1. O Terminal sem medo", st["h2"]))
    F.append(Paragraph(
        "O <b>Terminal</b> é um programa onde você digita instruções para o "
        "computador em vez de clicar. Parece intimidante, mas aqui você quase "
        "não vai digitar: nós preparamos um menu que faz o trabalho. O básico:",
        st["body"]))
    F.append(bullets([
        "Abrir o Terminal: aperte ⌘+barra-de-espaço (Spotlight), escreva "
        "“Terminal” e aperte ENTER.",
        "O “prompt” é a linha onde o cursor pisca, esperando você. Você digita "
        "(ou cola) algo e aperte ENTER para executar.",
        "Colar: ⌘+V. Copiar daqui e colar lá funciona normalmente.",
        "Nada acontece sozinho — só quando você aperta ENTER. Pode respirar.",
    ], st))

    F.append(Paragraph("2. Como usar este pacote (o jeito fácil)", st["h2"]))
    F.append(Paragraph(
        "Na pasta <b>Pipeline_Alunos</b> há um arquivo chamado "
        "<b>iniciar.command</b>. Dê <b>dois cliques</b> nele: abre uma janela com "
        "um menu em português. Escolha um número (0, 1, 2, …) e aperte ENTER. "
        "O menu explica cada passo e pergunta antes de fazer qualquer coisa.",
        st["body"]))
    F.append(Paragraph(
        "Na primeira vez, o macOS pode dizer que “não pode verificar o "
        "desenvolvedor”. Isso é normal para um arquivo nosso: clique com o botão "
        "direito em <b>iniciar.command</b> → <b>Abrir</b> → <b>Abrir</b>. Depois "
        "disso o duplo-clique funciona sempre.", st["warn"]))

    F.append(Paragraph("3. Os passos, um a um", st["h2"]))
    F.append(Paragraph("Faça <b>em ordem</b> (0 → 1 → 2 → …). Os passos 3, 5 e 6 "
                       "precisam do FreeSurfer (já instalado no computador de "
                       "Recon pelo responsável).", st["body"]))
    F.append(steps_table(st))

    F.append(Paragraph("4. Regras de ouro (segurança e FAIR)", st["h2"]))
    F.append(Paragraph(
        "Estes dados são de pacientes reais. Trate cada arquivo original como "
        "“somente leitura”.", st["rule"]))
    F.append(bullets([
        "Os dados NUNCA saem deste computador. Nada de nuvem pessoal "
        "(OneDrive/Drive/Dropbox), e-mail ou pen-drive não autorizado.",
        "Nunca apague ou edite os DICOM originais nem a pasta dos pacientes. "
        "O pipeline só cria arquivos novos.",
        "Só compartilhe algo depois do passo 2 (desidentificar) e do passo 3 "
        "(remoção facial) — e mesmo assim, somente com autorização.",
        "Em caso de dúvida ou erro que você não entende: PARE e chame o "
        "responsável. Não “tente consertar” no escuro.",
    ], st))

    F.append(Paragraph("5. Se algo der errado", st["h2"]))
    F.append(bullets([
        "“command not found” ou um passo do FreeSurfer falha: o FreeSurfer "
        "provavelmente não foi carregado nesta janela. Avise o responsável "
        "(é só rodar o comando de ativação uma vez).",
        "Pediu senha do Mac: isso é permissão do sistema — só prossiga se o "
        "responsável tiver orientado.",
        "O passo 5 está “travado” há muito tempo: provavelmente está só "
        "demorando (é normal levar horas). Não feche no susto; confirme com o "
        "responsável.",
        "Qualquer linha em vermelho com a palavra ERROR/MISMATCH: anote e "
        "mostre ao responsável antes de seguir.",
    ], st))

    F.append(Spacer(1, 8))
    F.append(HRFlowable(width="100%", thickness=0.6, color=GREY))
    F.append(Paragraph("Gerado por make_student_guide.py. Material instrutivo; não "
                       "contém dados de pacientes. Em português, para a equipe de "
                       "treinamento da UFMG.", st["small"]))
    doc.build(F)


def main():
    out = Path(__file__).resolve().parent / "GUIA_DO_ALUNO.pdf"
    build(str(out))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
