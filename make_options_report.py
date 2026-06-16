"""
make_options_report.py
=======================

One-page (Brazilian Portuguese) decision/status PDF on the two externally-
suggested options - OpenNeuro vs MINDSETS / neuro-path.ai - updated with the
local vetting+test outcome, as decision support for the discussion with
Prof. Bernardo Viana.

Reuses the shared styling from make_report.py. Contains NO patient data.

Run:
    python make_options_report.py     # -> deliverable/CBR_opcoes_pipeline_comparacao.pdf
"""

from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable)

from make_report import build_styles, esc, bullets, ACCENT, LIGHT, GREY


def comparison_table(st):
    cell = ParagraphStyle("c", parent=st["cell"], fontSize=8.2, leading=10)
    head = ParagraphStyle("ch", parent=st["cellh"], fontSize=8.6, leading=10.5)
    namest = ParagraphStyle("cn", parent=cell, fontName="Helvetica-Bold")

    header = ["Opção", "O que é / para quê", "Onde ficam os dados e risco",
              "Papel no nosso estudo"]
    rows = [
        ["OpenNeuro",
         "Repositório público de dados BIDS (FAIR; DOI; licença CC0). Não é "
         "método de análise.",
         "Saem da máquina (upload público, praticamente irreversível). Exige "
         "desidentificação + remoção facial (já temos), consentimento para dados "
         "abertos e aprovação CEP/CONEP; dado de saúde = sensível (LGPD).",
         "Etapa de PUBLICAÇÃO, não de análise. Prematuro para o piloto; "
         "revisitar mais adiante."],
        ["MINDSETS (SynthSeg)",
         "Segmentação cerebral SynthSeg (método publicado, open-source; também "
         "embutido no FreeSurfer 8 como mri_synthseg). Gera máscara + volumes "
         "regionais (volumes.csv). NÃO é o classificador de demência.",
         "Local/offline (Docker, --network none) = dados ficam aqui, nada sai. "
         "VERIFICADO 16/06: íntegro, sem exfiltração, antivírus limpo, roda "
         "100% offline. (O site ao vivo, em servidor MBZUAI/Emirados, NÃO deve "
         "receber dados reais.)",
         "COMPLEMENTO do FreeSurfer (não substitui): dá volumes subcorticais, "
         "mas não espessura cortical nem fluxo longitudinal. Bom para QC rápido "
         "e verificação cruzada de volumes. Uso interno, offline → baixo risco."],
    ]
    data = [[Paragraph(esc(c), head) for c in header]]
    for r in rows:
        data.append([Paragraph(esc(r[0]), namest)] +
                     [Paragraph(esc(c), cell) for c in r[1:]])
    t = Table(data, colWidths=[24*mm, 44*mm, 56*mm, 46*mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c5d2e0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def build(path):
    st = build_styles()
    okbox = ParagraphStyle("ok", parent=st["body"], fontSize=9, leading=12,
                           backColor=colors.HexColor("#eaf6ec"),
                           borderColor=colors.HexColor("#2e7d32"),
                           borderWidth=0.8, borderPadding=7, spaceAfter=6)
    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=16*mm, rightMargin=16*mm,
                            topMargin=13*mm, bottomMargin=13*mm,
                            title="Opções de pipeline — decisão e status")
    F = []
    F.append(Paragraph("Opções de pipeline — decisão e status",
                       ParagraphStyle("t", parent=st["title"], fontSize=18,
                                      leading=21)))
    F.append(Paragraph("OpenNeuro (repositório FAIR) &nbsp;vs&nbsp; "
                       "MINDSETS / neuro-path.ai (segmentação SynthSeg)",
                       st["subtitle"]))
    F.append(Spacer(1, 3))
    F.append(Paragraph(f"Preparado em {date.today().isoformat()} &nbsp;&middot;&nbsp; "
                       "apoio à decisão para discussão com o Prof. Bernardo Viana "
                       "&nbsp;&middot;&nbsp; <b>não contém dados de pacientes</b>",
                       st["small"]))
    F.append(HRFlowable(width="100%", thickness=1, color=ACCENT,
                        spaceBefore=6, spaceAfter=6))

    F.append(Paragraph(
        "✔ <b>MINDSETS verificado e testado localmente (16/06/2026):</b> "
        "repositório autêntico (MBZUAI), sem código de exfiltração, antivírus "
        "limpo (0 infectados), constrói e roda <b>100% offline</b> "
        "(--network none) e gera volumes.csv com ~34 regiões (hipocampo, tálamo, "
        "córtex…). É <b>segmentação SynthSeg — não o classificador de demência</b>, "
        "então não há risco de mudança de domínio.", okbox))

    F.append(Paragraph(
        "As duas opções resolvem <b>problemas diferentes</b> e <b>nenhuma "
        "substitui</b> o pipeline estrutural do FreeSurfer (núcleo da análise "
        "estrutura ↔ cognição). Pergunta central: <b>para onde vão os dados?</b> "
        "O que roda localmente é de baixo risco; o que faz upload exige CEP/CONEP, "
        "LGPD e consentimento.", st["body"]))

    F.append(comparison_table(st))
    F.append(Spacer(1, 7))

    F.append(Paragraph(
        "<b>Estratégia recomendada:</b> manter o <b>FreeSurfer 8 longitudinal "
        "como núcleo</b> (dá espessura cortical + fluxo longitudinal, mais "
        "sensível à mudança). Usar o <b>MINDSETS/SynthSeg como complemento "
        "opcional</b> (QC rápido + verificação cruzada de volumes), em "
        "derivatives/, uso local/offline → decisão interna + aval do Prof. Viana. "
        "<b>OpenNeuro = etapa futura</b> de publicação.", st["rec"]))

    F.append(Paragraph("Como o MINDSETS integra (não substitui)", st["h2"]))
    F.append(bullets([
        "Mesmo T1 desfacializado entra → volumes.csv sai em "
        "BIDS_output/derivatives/mindsets/, ao lado das saídas do FreeSurfer; "
        "ambos alimentam a mesma tabela de efeitos mistos.",
        "O FreeSurfer continua dando o que o SynthSeg não dá: espessura/superfície "
        "cortical e o fluxo longitudinal.",
        "O SynthSeg agrega velocidade e robustez: triagem/QC em minutos e plano B "
        "se um exame falhar no recon do FreeSurfer.",
    ], st))

    F.append(Paragraph("Decisões para o Prof. Bernardo Viana", st["h2"]))
    F.append(bullets([
        "Aprovar o uso interno do MINDSETS como complemento (roda offline; nada "
        "sai da máquina)?",
        "Autorizar uma verificação em 1 T1 real desidentificado e desfacializado, "
        "para confirmar volumes plausíveis?",
        "Confirmar a licença do MINDSETS para uso em pesquisa (com a equipe da "
        "MBZUAI).",
        "OpenNeuro: adiar para a fase de publicação (exige CEP/CONEP, LGPD, "
        "consentimento)? Confirmar.",
    ], st))

    F.append(Spacer(1, 5))
    F.append(HRFlowable(width="100%", thickness=0.6, color=GREY))
    F.append(Paragraph("Gerado por make_options_report.py. Resumo de apoio à "
                       "decisão; não contém dados de pacientes e pode ser "
                       "compartilhado com a equipe do projeto.", st["small"]))

    doc.build(F)


def main():
    out_dir = Path(__file__).resolve().parent / "deliverable"
    out_dir.mkdir(exist_ok=True)
    out_path = str(out_dir / "CBR_opcoes_pipeline_comparacao.pdf")
    build(out_path)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
