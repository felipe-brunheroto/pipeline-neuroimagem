"""
make_options_report.py
=======================

One-page PDF (Brazilian Portuguese) comparing the two externally-suggested
pipeline options - OpenNeuro vs MINDSETS / neuro-path.ai - as decision support
for the discussion with Prof. Bernardo Viana.

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
              "Adequação ao nosso estudo"]
    rows = [
        ["OpenNeuro",
         "Repositório público de dados BIDS (FAIR; DOI; licença CC0) + acesso a "
         "pipelines BIDS padronizados. Não é método de análise nem específico de "
         "demência.",
         "Saem da máquina (upload público, praticamente irreversível). Exige "
         "desidentificação + remoção facial (já temos), consentimento para dados "
         "abertos e aprovação CEP/CONEP; dado de saúde = dado sensível (LGPD).",
         "É etapa de publicação, não de análise. Prematuro para o piloto; "
         "revisitar na fase de publicação."],
        ["MINDSETS / neuro-path.ai",
         "Ferramenta de segmentação + classificação voltada para demência. Tem "
         "site ao vivo (CPU), ferramenta para download e configuração em Docker "
         "(execução local).",
         "Docker/ferramenta local = dados ficam aqui (seguro). Site ao vivo = "
         "envia dados a servidor externo (MBZUAI / Emirados) — não usar com dados "
         "reais. Verificar o container (sem chamadas de rede), a integridade do "
         "download e a licença.",
         "Treinado para demência; nossa coorte é déficit cognitivo pós-COVID → "
         "risco de mudança de domínio. Útil como segmentador ou biomarcador "
         "exploratório; interpretar a classificação com cautela. Confirmar o que "
         "ele gera e se é validado/publicado."],
    ]
    data = [[Paragraph(esc(c), head) for c in header]]
    for r in rows:
        data.append([Paragraph(esc(r[0]), namest)] +
                     [Paragraph(esc(c), cell) for c in r[1:]])
    t = Table(data, colWidths=[22*mm, 49*mm, 53*mm, 46*mm], repeatRows=1)
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
    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=18*mm, rightMargin=18*mm,
                            topMargin=14*mm, bottomMargin=14*mm,
                            title="Opções de pipeline — comparação")
    F = []
    F.append(Paragraph("Opções de pipeline de neuroimagem — comparação",
                       ParagraphStyle("t", parent=st["title"], fontSize=18,
                                      leading=21)))
    F.append(Paragraph("OpenNeuro (repositório FAIR) &nbsp;vs&nbsp; "
                       "MINDSETS / neuro-path.ai (classificador de demência)",
                       st["subtitle"]))
    F.append(Spacer(1, 3))
    F.append(Paragraph(f"Preparado em {date.today().isoformat()} &nbsp;&middot;&nbsp; "
                       "apoio à decisão para discussão com o Prof. Bernardo Viana "
                       "&nbsp;&middot;&nbsp; <b>não contém dados de pacientes</b>",
                       st["small"]))
    F.append(HRFlowable(width="100%", thickness=1, color=ACCENT,
                        spaceBefore=6, spaceAfter=6))

    F.append(Paragraph(
        "As duas opções resolvem <b>problemas diferentes</b> e <b>nenhuma "
        "substitui</b> o nosso pipeline estrutural do FreeSurfer (o núcleo da "
        "análise estrutura ↔ cognição). A pergunta central: <b>para onde vão os "
        "dados?</b> O que roda localmente é de baixo risco; o que faz upload exige "
        "aprovação CEP/CONEP, base legal LGPD e consentimento.", st["body"]))

    F.append(comparison_table(st))
    F.append(Spacer(1, 8))

    F.append(Paragraph(
        "<b>Recomendação:</b> manter a análise principal no pipeline local do "
        "FreeSurfer. Tratar o MINDSETS como complemento opcional, executado "
        "<b>apenas localmente</b> (Docker isolado da rede, verificado). Tratar o "
        "OpenNeuro como etapa <b>futura</b> de publicação, condicionada à "
        "aprovação ética/consentimento — não agora.", st["rec"]))

    F.append(Paragraph("Perguntas para o Prof. Bernardo Viana", st["h2"]))
    F.append(bullets([
        "O objetivo imediato é obter resultados de análise ou compartilhar dados? "
        "(define se o OpenNeuro entra em cena agora)",
        "Qual o status ético/consentimento para qualquer compartilhamento externo "
        "— aprovação CEP/CONEP, base legal LGPD, consentimento para dados abertos "
        "e acordo de transferência internacional para a MBZUAI (Emirados)?",
        "MINDSETS: podemos rodar o Docker localmente/offline? O que exatamente ele "
        "gera (segmentação? probabilidade? rótulo?)? É publicado/validado? A "
        "licença permite uso em pesquisa?",
        "Sendo treinado para demência, como validar sua relevância para uma coorte "
        "pós-COVID antes de confiar em qualquer classificação?",
    ], st))

    F.append(Spacer(1, 6))
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
