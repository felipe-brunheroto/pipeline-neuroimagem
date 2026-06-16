"""
make_report.py
==============

Generate a polished, shareable PDF that summarizes the pipeline and gives the
recon-side run guide. The PDF is meant to be shown on an iPad (Google Drive /
Safari / Files all preview PDF natively) and handed to the team.

Rendered text is in Brazilian Portuguese (the audience is the UFMG team);
code, comments and shell commands stay in English. It contains CODE/WORKFLOW
description only - NO patient data, no images, no identifiers.

Run:
    python make_report.py                 # -> deliverable/CBR_NEUROCOVID_pipeline_overview.pdf
"""

import os
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Preformatted, HRFlowable,
                                ListFlowable, ListItem)

ACCENT = colors.HexColor("#1f4e79")
LIGHT = colors.HexColor("#eaf0f6")
GREY = colors.HexColor("#555555")


def esc(text):
    # reportlab's mini-markup needs & and < escaped; a bare > renders fine (and
    # escaping it would show a literal "&gt;"), so leave > alone.
    return text.replace("&", "&amp;").replace("<", "&lt;")


def build_styles():
    ss = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("title", parent=ss["Title"], textColor=ACCENT,
                                fontSize=22, leading=26, spaceAfter=2),
        "subtitle": ParagraphStyle("subtitle", parent=ss["Normal"],
                                   textColor=GREY, fontSize=11, leading=14),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], textColor=ACCENT,
                             fontSize=13.5, leading=16, spaceBefore=14,
                             spaceAfter=6),
        "body": ParagraphStyle("body", parent=ss["BodyText"], fontSize=10,
                               leading=14, spaceAfter=6),
        "small": ParagraphStyle("small", parent=ss["Normal"], fontSize=8.5,
                                leading=11, textColor=GREY),
        "code": ParagraphStyle("code", parent=ss["Code"], fontSize=8.6,
                               leading=11.5, textColor=colors.HexColor("#11304a"),
                               backColor=LIGHT, borderPadding=6, spaceBefore=2,
                               spaceAfter=8, leftIndent=2),
        "cellh": ParagraphStyle("cellh", parent=ss["Normal"], fontSize=9.2,
                                leading=12, textColor=colors.white,
                                fontName="Helvetica-Bold"),
        "cell": ParagraphStyle("cell", parent=ss["Normal"], fontSize=9,
                               leading=12),
        "rec": ParagraphStyle("rec", parent=ss["BodyText"], fontSize=9.6,
                              leading=13, backColor=colors.HexColor("#eaf6ec"),
                              borderColor=colors.HexColor("#2e7d32"),
                              borderWidth=0.8, borderPadding=8, spaceBefore=4,
                              spaceAfter=6),
    }
    return styles


def bullets(items, st, style="body"):
    return ListFlowable(
        [ListItem(Paragraph(esc(t), st[style]), leftIndent=10) for t in items],
        bulletType="bullet", start="circle", leftIndent=12)


def scripts_table(st):
    rows = [
        ["Script", "O que faz", "Onde roda"],
        ["check_environment.py", "Verifica se Python / dcm2niix / pydicom estão prontos.", "Ambos"],
        ["dicom_to_bids.py", "DICOM → NIfTI (dcm2niix); organiza em anat/dwi/fmap/perf/swi/misc; gera proveniência + validação.", "Mac do Felipe"],
        ["deidentify_sidecars.py", "Remove identificadores de instituição/equipamento dos sidecars JSON (perfil DICOM PS3.15).", "Mac do Felipe"],
        ["make_bids.py", "Monta o conjunto BIDS FAIR e validado das imagens anatômicas (bids/).", "Mac do Felipe"],
        ["deface_anat.py", "Remoção facial de T1/T2/FLAIR com o mideface do FreeSurfer; mantém os originais.", "Mac do recon"],
        ["run_recon_long.py", "Conduz o fluxo longitudinal do FreeSurfer 8 (cross → base → long).", "Mac do recon"],
    ]
    data = [[Paragraph(esc(c), st["cellh"] if i == 0 else st["cell"])
             for c in row] for i, row in enumerate(rows)]
    t = Table(data, colWidths=[37*mm, 103*mm, 24*mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c5d2e0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def flow_bar(st):
    stages = ["Converter", "Anonimizar", "Remoção\nfacial", "BIDS (FAIR)",
              "Transferir", "recon-all\n(long.)", "QC", "Estatística"]
    cells = [Paragraph(esc(s).replace("\n", "<br/>"),
                       ParagraphStyle("f", parent=st["cell"], alignment=1,
                                      fontSize=8.4, leading=10,
                                      textColor=colors.white,
                                      fontName="Helvetica-Bold")) for s in stages]
    t = Table([cells], colWidths=[(170/len(stages))*mm]*len(stages))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (4, 0), ACCENT),
        ("BACKGROUND", (5, 0), (-1, 0), colors.HexColor("#2e7d32")),
        ("GRID", (0, 0), (-1, -1), 1.2, colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def code(text, st):
    return Preformatted(esc(text), st["code"])


VISIT = {1: "basal", 2: "6 meses", 3: "12 meses"}


def visit_of(session_label):
    import re
    m = re.search(r"(\d+)", session_label or "")
    idx = int(m.group(1)) if m else 1
    return VISIT.get(idx, f"{(idx - 1) * 6} meses")


def load_stats(output_dir):
    """Derive the dataset figures from provenance.csv so the report never goes
    stale. Returns None if there is no provenance yet (e.g. code-only machine)."""
    import csv
    prov = os.path.join(output_dir, "provenance.csv")
    if not os.path.isfile(prov):
        return None
    subs, n_series, n_anat = {}, 0, 0
    with open(prov, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            n_series += 1
            subs.setdefault(r["subject"], set()).add(r["session"])
            if r.get("category") == "anat":
                n_anat += 1
    sessions = {s: sorted(v) for s, v in subs.items()}
    multi = {s: v for s, v in sessions.items() if len(v) >= 2}
    return {
        "sessions": sessions,
        "n_sub": len(sessions),
        "n_ses": sum(len(v) for v in sessions.values()),
        "n_series": n_series,
        "n_anat": n_anat,
        "multi": sorted(multi),
        "plan": {"cross": sum(len(v) for v in sessions.values()),
                 "base": len(multi),
                 "long": sum(len(v) for v in multi.values())},
    }


def data_bullets(stats):
    """Section 3 bullets, built from real numbers when available (pt-BR)."""
    if stats is None:
        counts = ("As contagens de participantes/sessões/séries são preenchidas a "
                  "partir de provenance.csv assim que a conversão rodar na máquina "
                  "de dados.")
        anat = "as anatômicas"
    else:
        counts = (f"{stats['n_sub']} participantes, {stats['n_ses']} sessões de "
                  f"imagem, {stats['n_series']} séries convertidas; tudo rastreável "
                  "via provenance.csv e conferido em validation_report.csv.")
        anat = f"T1w / T2w / FLAIR, {stats['n_anat']} aquisições"
    visit_line = ("Esquema de visitas longitudinais codificado como sessões BIDS: "
                  "ses-1 = basal, ses-2 = 6 meses, ses-3 = 12 meses.")
    if stats is not None:
        per = "; ".join(
            f"{sub.replace('sub-', '')}: " + ", ".join(visit_of(s) for s in ses)
            for sub, ses in sorted(stats["sessions"].items()))
        visit_line += f" Por participante — {per}."
    return [
        counts,
        visit_line,
        "Modalidades organizadas em anat / dwi / fmap / perf (ASL) / swi / misc.",
        f"Conjunto BIDS (bids/) por enquanto só com anatômicas ({anat}), validado, "
        "com metadados dataset_description / participants / sessions. Os sidecars "
        "JSON não contêm nenhum identificador.",
        "Remoção facial já implementada (mideface do FreeSurfer), apenas "
        "anatômicas, com os originais preservados.",
    ]


def plan_bullets(stats):
    """Section 4 recon-plan bullets, derived from the session structure (pt-BR)."""
    if stats is None:
        return ["uma execução transversal por tempo,",
                "um template intra-participante não enviesado por participante com "
                "múltiplas visitas,",
                "uma execução longitudinal por tempo desses participantes."]
    p = stats["plan"]
    subj = ", ".join(s.replace("sub-", "") for s in stats["multi"]) or "nenhum ainda"
    return [
        f"{p['cross']} execuções transversais (T1 + superfície pial refinada com FLAIR),",
        f"{p['base']} templates intra-participante ({subj} — os participantes com "
        "múltiplas visitas),",
        f"{p['long']} execuções longitudinais.",
    ]


def build(path, stats):
    st = build_styles()
    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=16*mm, bottomMargin=16*mm,
                            title="Visão geral do pipeline CBR / NEUROCOVID")
    F = []

    F.append(Paragraph("Pipeline de neuroimagem CBR / NEUROCOVID", st["title"]))
    F.append(Paragraph("DICOM → NIfTI → BIDS desidentificado → remoção facial "
                       "→ análise estrutural longitudinal no FreeSurfer 8",
                       st["subtitle"]))
    F.append(Spacer(1, 4))
    F.append(Paragraph(f"Preparado em {date.today().isoformat()} &nbsp;&middot;&nbsp; "
                       "para a equipe do projeto &nbsp;&middot;&nbsp; "
                       "<b>não contém dados de pacientes</b>", st["small"]))
    F.append(HRFlowable(width="100%", thickness=1, color=ACCENT,
                        spaceBefore=8, spaceAfter=8))

    F.append(Paragraph("1. O que é isto", st["h2"]))
    F.append(Paragraph(
        "Um pipeline pequeno, executado apenas localmente, que transforma os "
        "DICOMs brutos do scanner em um conjunto de dados organizado, "
        "desidentificado e alinhado aos princípios FAIR, e realiza a análise "
        "estrutural do cérebro. O objetivo: comparar cada participante ao longo "
        "dos tempos (longitudinal) e relacionar a estrutura cerebral ao status "
        "clínico (entre participantes). Tudo abaixo está construído, testado e "
        "versionado. A conversão/curadoria roda no Mac do Felipe; o recon pesado "
        "do FreeSurfer roda no Mac dedicado (M4 / 64 GB).", st["body"]))

    F.append(Paragraph("2. O pipeline em um relance", st["h2"]))
    F.append(flow_bar(st))
    F.append(Spacer(1, 8))
    F.append(scripts_table(st))

    F.append(Paragraph("3. Como estão os dados agora", st["h2"]))
    F.append(bullets(data_bullets(stats), st))

    F.append(Paragraph("4. Plano de análise estrutural (FreeSurfer 8)", st["h2"]))
    pilot = (f"Amostra pequena (N={stats['n_sub']}) = piloto" if stats
             else "Amostra pequena = piloto")
    F.append(Paragraph(
        "O FreeSurfer 8 roda <b>nativamente no Apple Silicon</b> (sem Rosetta), "
        "então o Mac M4 / 64 GB é uma boa escolha; o FastSurfer não é necessário "
        "(sua vantagem são GPUs CUDA, que os Macs não têm). Usamos o fluxo "
        "longitudinal (Reuter et al., 2012): uma execução transversal por tempo, "
        "um template intra-participante não enviesado e, então, uma execução "
        "longitudinal por tempo. Para este conjunto de dados, isso significa:",
        st["body"]))
    F.append(bullets(plan_bullets(stats), st))
    F.append(Paragraph(
        "Participantes com uma única visita recebem apenas a execução transversal "
        "e entram no fluxo longitudinal automaticamente quando os retornos "
        f"chegarem. <b>{pilot}</b> — planeje análise de ROIs guiada por hipótese "
        "com modelos de efeitos mistos, não vértice a vértice no cérebro inteiro.",
        st["body"]))

    F.append(Paragraph("5. Guia de execução — Mac do recon (FreeSurfer 8)", st["h2"]))
    F.append(Paragraph("Pré-requisito: instale o FreeSurfer 8 (build macOS "
                       "<b>arm64</b>) e coloque sua licença gratuita em "
                       "$FREESURFER_HOME/license.txt. Python 3 + dcm2niix via "
                       "<font face='Courier'>pip install -r requirements.txt</font>. "
                       "Depois:", st["body"]))
    F.append(code(
        "# 0)  Carregue o FreeSurfer 8 (ajuste o caminho da sua instalação)\n"
        "export FREESURFER_HOME=/Applications/freesurfer/8.0.0\n"
        "source $FREESURFER_HOME/SetUpFreeSurfer.sh\n\n"
        "# 1)  Pré-visualize o plano (não precisa do FreeSurfer para isto)\n"
        "python3 run_recon_long.py --dry-run\n\n"
        "# 2)  Remocao facial das anatomicas (gera derivatives/defaced/, mantem originais)\n"
        "python3 deface_anat.py\n\n"
        "# 3)  Rode o fluxo longitudinal sobre as anatomicas com remocao facial\n"
        "python3 run_recon_long.py \\\n"
        "        --input BIDS_output/derivatives/defaced \\\n"
        "        --subjects-dir ~/fs_subjects --jobs 4 --threads 4\n\n"
        "# 4)  QC das superficies, depois extraia metricas para estatistica\n"
        "freeview ...                 # verificacao visual; anote o numero de Euler\n"
        "asegstats2table / aparcstats2table   # -> tabelas de ROI organizadas", st))
    F.append(Paragraph("~8 GB de RAM e alguns núcleos por job; <font face='Courier'>"
                       "--jobs 4</font> é confortável em 64 GB. Execuções já "
                       "concluídas são puladas automaticamente.", st["small"]))

    F.append(Paragraph("6. Observações e ressalvas", st["h2"]))
    F.append(bullets([
        "Os dados de pacientes nunca viajam com este pacote. Mova DICOMs/imagens "
        "derivadas apenas por meios institucionais aprovados — nunca por nuvem "
        "pessoal.",
        "Faça a remoção facial antes de as anatômicas saírem da máquina segura; "
        "use o mesmo método em todos os tempos para manter as comparações "
        "longitudinais consistentes.",
        "Um 'RepetitionTime MISMATCH' benigno na série de difusão derivada TRACEW "
        "é esperado (comportamento do dcm2niix); os dados de imagem não são "
        "afetados.",
        "Verifique a consistência de scanner (Fabricante / intensidade de campo "
        "são mantidos) — se os participantes usarem scanners diferentes, harmonize "
        "(ComBat / longCombat) antes da estatística.",
        "Valide o bids/ com o validador oficial no Mac do recon: "
        "deno run -A jsr:@bids/validator bids",
    ], st))

    F.append(Spacer(1, 10))
    F.append(HRFlowable(width="100%", thickness=0.6, color=GREY))
    F.append(Paragraph("Gerado por make_report.py. O código é versionado; este PDF "
                       "e os scripts que o acompanham não contêm dados de pacientes "
                       "e podem ser compartilhados com a equipe do projeto.",
                       st["small"]))

    doc.build(F)


def main():
    here = Path(__file__).resolve().parent
    stats = load_stats(str(here / "BIDS_output"))
    out_dir = here / "deliverable"
    out_dir.mkdir(exist_ok=True)
    out_path = str(out_dir / "CBR_NEUROCOVID_pipeline_overview.pdf")
    build(out_path, stats)
    if stats:
        print(f"wrote {out_path}  ({stats['n_sub']} subjects, "
              f"{stats['n_ses']} sessions, {stats['n_series']} series)")
    else:
        print(f"wrote {out_path}  (no provenance.csv found; generic counts)")


if __name__ == "__main__":
    main()
