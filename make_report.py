"""
make_report.py
==============

Generate a polished, shareable PDF that summarizes the pipeline and gives the
recon-side run guide. The PDF is meant to be shown on an iPad (Google Drive /
Safari / Files all preview PDF natively) and handed to the FreeSurfer senior.

It contains CODE/WORKFLOW description only - NO patient data, no images, no
identifiers (subject codes like sub-CBR003 are de-identified study codes).

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
    }
    return styles


def bullets(items, st):
    return ListFlowable(
        [ListItem(Paragraph(esc(t), st["body"]), leftIndent=10) for t in items],
        bulletType="bullet", start="circle", leftIndent=12)


def scripts_table(st):
    rows = [
        ["Script", "What it does", "Runs on"],
        ["check_environment.py", "Verifies Python / dcm2niix / pydicom are ready.", "Either"],
        ["dicom_to_bids.py", "DICOM → NIfTI (dcm2niix); sorts into anat/dwi/fmap/perf/swi/misc; writes provenance + validation.", "Felipe's Mac"],
        ["deidentify_sidecars.py", "Strips site/scanner identifiers from JSON sidecars (DICOM PS3.15 profile).", "Felipe's Mac"],
        ["make_bids.py", "Builds the FAIR, validator-clean BIDS dataset of anatomicals (bids/).", "Felipe's Mac"],
        ["deface_anat.py", "Defaces T1/T2/FLAIR with FreeSurfer mideface; originals kept.", "Recon Mac"],
        ["run_recon_long.py", "Drives the FreeSurfer 8 longitudinal stream (cross → base → long).", "Recon Mac"],
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
    stages = ["Convert", "De-identify", "Deface", "BIDS (FAIR)",
              "Transfer", "recon-all\n(long.)", "QC", "Stats"]
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


def build(path):
    st = build_styles()
    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=16*mm, bottomMargin=16*mm,
                            title="CBR / NEUROCOVID pipeline overview")
    F = []

    F.append(Paragraph("CBR / NEUROCOVID neuroimaging pipeline", st["title"]))
    F.append(Paragraph("DICOM → NIfTI → de-identified BIDS → defacing "
                       "→ FreeSurfer 8 longitudinal structural analysis",
                       st["subtitle"]))
    F.append(Spacer(1, 4))
    F.append(Paragraph(f"Prepared {date.today().isoformat()} &nbsp;&middot;&nbsp; "
                       "for the FreeSurfer recon lead &nbsp;&middot;&nbsp; "
                       "<b>contains no patient data</b>", st["small"]))
    F.append(HRFlowable(width="100%", thickness=1, color=ACCENT,
                        spaceBefore=8, spaceAfter=8))

    F.append(Paragraph("1. What this is", st["h2"]))
    F.append(Paragraph(
        "A small, local-only pipeline that turns raw scanner DICOMs into an "
        "organized, de-identified, FAIR-aligned dataset and runs structural "
        "brain analysis. The goal: compare each subject across timepoints "
        "(longitudinal) and relate brain structure to clinical status "
        "(between-subject). Everything below is built, tested, and version "
        "controlled. Conversion/curation runs on Felipe's Mac; the heavy "
        "FreeSurfer recon runs on the dedicated M4 / 64 GB Mac.", st["body"]))

    F.append(Paragraph("2. The pipeline at a glance", st["h2"]))
    F.append(flow_bar(st))
    F.append(Spacer(1, 8))
    F.append(scripts_table(st))

    F.append(Paragraph("3. What the data looks like now", st["h2"]))
    F.append(bullets([
        "4 subjects, 6 imaging sessions, 102 series converted; everything "
        "traceable via provenance.csv and checked in validation_report.csv.",
        "Longitudinal visit scheme encoded as BIDS sessions: ses-1 = baseline, "
        "ses-2 = 6-month, ses-3 = 12-month. (CBR003 & CBR011 have 6- and "
        "12-month visits; CBR052 & CBR053 have baseline.)",
        "Modalities sorted into anat / dwi / fmap / perf (ASL) / swi / misc.",
        "BIDS dataset (bids/) is anatomical-only for now (T1w / T2w / FLAIR, "
        "18 scans), validator-clean, with dataset_description / participants / "
        "sessions metadata. JSON sidecars carry zero identifiers.",
        "Defacing is in place (FreeSurfer mideface), anat only, originals kept.",
    ], st))

    F.append(Paragraph("4. Structural analysis plan (FreeSurfer 8)", st["h2"]))
    F.append(Paragraph(
        "FreeSurfer 8 runs <b>natively on Apple Silicon</b> (no Rosetta), so "
        "the M4 / 64 GB Mac is a good fit; FastSurfer is not needed (its "
        "advantage is CUDA GPUs, which Macs lack). We use the longitudinal "
        "stream (Reuter et al., 2012): a cross-sectional run per timepoint, an "
        "unbiased within-subject template, then a longitudinal run per "
        "timepoint. For this dataset that is:", st["body"]))
    F.append(bullets([
        "6 cross-sectional runs (T1 + FLAIR-refined pial surface),",
        "2 within-subject templates (CBR003, CBR011 — the multi-visit subjects),",
        "4 longitudinal runs.",
    ], st))
    F.append(Paragraph(
        "Single-visit subjects get the cross-sectional run only and slot into "
        "the longitudinal stream automatically once follow-ups arrive. "
        "<b>N=4 is a pilot</b> — plan for hypothesis-driven ROI analysis with "
        "mixed-effects models, not whole-brain vertexwise.", st["body"]))

    F.append(Paragraph("5. Run guide — recon Mac (FreeSurfer 8)", st["h2"]))
    F.append(Paragraph("Prerequisite: install FreeSurfer 8 (macOS <b>arm64</b> "
                       "build) and place your free license at "
                       "$FREESURFER_HOME/license.txt. Python 3 + dcm2niix via "
                       "<font face='Courier'>pip install -r requirements.txt</font>. "
                       "Then:", st["body"]))
    F.append(code(
        "# 0)  Source FreeSurfer 8 (adjust the path to your install)\n"
        "export FREESURFER_HOME=/Applications/freesurfer/8.0.0\n"
        "source $FREESURFER_HOME/SetUpFreeSurfer.sh\n\n"
        "# 1)  Preview the plan first (no FreeSurfer needed for this)\n"
        "python3 run_recon_long.py --dry-run\n\n"
        "# 2)  Deface the anatomicals (writes derivatives/defaced/, keeps originals)\n"
        "python3 deface_anat.py\n\n"
        "# 3)  Run the longitudinal stream on the defaced anat\n"
        "python3 run_recon_long.py \\\n"
        "        --input BIDS_output/derivatives/defaced \\\n"
        "        --subjects-dir ~/fs_subjects --jobs 4 --threads 4\n\n"
        "# 4)  QC the surfaces, then extract metrics for stats\n"
        "freeview ...                 # visual check; note the Euler number\n"
        "asegstats2table / aparcstats2table   # -> tidy ROI tables", st))
    F.append(Paragraph("~8 GB RAM and a few cores per job; <font face='Courier'>"
                       "--jobs 4</font> is comfortable on 64 GB. Completed runs "
                       "are skipped automatically.", st["small"]))

    F.append(Paragraph("6. Notes &amp; caveats", st["h2"]))
    F.append(bullets([
        "Patient data never travels with this bundle. Move DICOMs/derived "
        "imaging only by approved institutional means — never personal cloud.",
        "Deface before the structurals leave the secure machine; deface every "
        "timepoint the same way to keep longitudinal comparisons consistent.",
        "A benign 'RepetitionTime MISMATCH' on the derived diffusion TRACEW "
        "series is expected (dcm2niix behavior); image data is unaffected.",
        "Check scanner consistency (Manufacturer / field strength are retained) "
        "— if subjects span scanners, harmonize (ComBat / longCombat) before stats.",
        "Validate bids/ with the official validator on the recon Mac: "
        "deno run -A jsr:@bids/validator bids",
    ], st))

    F.append(Spacer(1, 10))
    F.append(HRFlowable(width="100%", thickness=0.6, color=GREY))
    F.append(Paragraph("Generated by make_report.py. Code is version-controlled; "
                       "this PDF and the accompanying scripts contain no patient "
                       "data and are safe to share with the project team.",
                       st["small"]))

    doc.build(F)


def main():
    out_dir = Path(__file__).resolve().parent / "deliverable"
    out_dir.mkdir(exist_ok=True)
    out_path = str(out_dir / "CBR_NEUROCOVID_pipeline_overview.pdf")
    build(out_path)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
