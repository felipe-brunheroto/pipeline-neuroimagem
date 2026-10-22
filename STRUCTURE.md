# Project structure & where everything lives

Everything the pipeline produces for the study lives under **one** folder,
`bids/` — a single, self-contained [BIDS](https://bids.neuroimaging.io/) dataset
that is ready to hand to an open-science platform (e.g. OpenNeuro). The split you
see inside it (`sub-*` vs `sourcedata/` vs `derivatives/`) is **standard BIDS,
not clutter**: raw data, source/working data, and processed outputs are meant to
be separate subtrees. What ties one patient's data together across all of them is
the **shared `sub-XXX` label** — it is identical in every subtree, so everything
for one person is one `grep`/glob away.

## The whole project at a glance

```
Pipeline Neuroimagem/
├── code/                      # all pipeline scripts + config (paths.py, series_map.csv, …)
├── Patient Files/             # raw DICOM input — one folder per exam. READ-ONLY, never shared.
├── bids/                      # THE dataset (everything below is here)
│   ├── dataset_description.json, README, CHANGES, participants.tsv/.json, .bidsignore
│   ├── sub-CBR003/ … sub-CBR053/        # raw BIDS (the OpenNeuro upload target)
│   │   └── ses-1/ … /anat/  func/
│   ├── sourcedata/
│   │   ├── conversion/         # the working/pre-BIDS tree: descriptive-named NIfTIs,
│   │   │                       #   provenance.csv, validation_report.csv, logs, reports/
│   │   ├── asl/  fmap/  func/  unknown/   # partially-curated / held-for-review series
│   └── derivatives/
│       ├── scanner-derived/    # ADC, TRACEW, SWI family, perfusion maps
│       ├── defaced/            # face-masked anatomicals (desc-defaced)
│       └── freesurfer/         # recon-all output (+ stats/, logs/)
├── Pipeline_Alunos/           # student teaching package (pt-BR guided menu)
├── deliverable/               # generated PDFs + handoff/export bundles (no patient data)
├── docs/                      # project documents (summary .docx, notes)
├── archive/                   # timestamped backups of previous runs (kept local)
├── README.md  QUICKSTART.md  CLAUDE.md  STRUCTURE.md  requirements.txt
```

> **code travels, data does not.** `code/`, `Pipeline_Alunos/`, and the docs are
> safe to share/version-control. `Patient Files/`, `bids/`, and `archive/` are
> patient-derived and are git-ignored — they stay on the secure machine.

## Which step writes where

Run scripts as `python code/<script>.py` from the project root. Defaults come
from [`code/paths.py`](code/paths.py); no path arguments are needed.

| # | Step (script) | Reads | Writes |
|---|---------------|-------|--------|
| 1 | `dicom_to_bids.py` | `Patient Files/` | `bids/sourcedata/conversion/` (NIfTIs, `provenance.csv`, `validation_report.csv`, `reports/`) |
| 2 | `deidentify_sidecars.py` | `bids/sourcedata/conversion/` | same (scrubs `*.json` in place; writes `deidentification_log.txt`) |
| 3 | `make_bids.py` | `…/conversion/provenance.csv` | `bids/sub-*` (raw anat/func) + dataset metadata |
| 4 | `make_derivatives.py` | `…/conversion/` | `bids/derivatives/scanner-derived/` + `bids/sourcedata/{asl,fmap,unknown}/` |
| 5 | `deface_anat.py` | `bids/sub-*` | `bids/derivatives/defaced/` |
| 6 | `run_recon_long.py` | `bids/` (or `…/defaced`) | `bids/derivatives/freesurfer/` |
| 7 | `extract_fs_stats.py` | `bids/derivatives/freesurfer/` | `bids/derivatives/freesurfer/stats/` |
| — | `export_openneuro.py` | `bids/` | validation report + (optional) `deliverable/bids_openneuro/` |

Re-running steps 3 and 4 is **safe**: each rebuilds only the subtree it owns and
leaves the others (including the FreeSurfer recon) untouched.

## Tracing one patient (example: `sub-CBR003`)

| What | Where |
|------|-------|
| Raw BIDS anatomicals (T1w/T2w/FLAIR) | `bids/sub-CBR003/ses-*/anat/` |
| Original conversion (descriptive scanner names) | `bids/sourcedata/conversion/sub-CBR003/ses-*/` |
| Link from each NIfTI back to its DICOM series | `bids/sourcedata/conversion/provenance.csv` (rows where `subject=sub-CBR003`) |
| Scanner-derived maps (ADC/TRACEW/SWI/perfusion) | `bids/derivatives/scanner-derived/sub-CBR003/` |
| Defaced anatomicals | `bids/derivatives/defaced/sub-CBR003/` |
| FreeSurfer surfaces/volumes | `bids/derivatives/freesurfer/sub-CBR003_ses-*/` |
| Region tables (volume/thickness) | `bids/derivatives/freesurfer/stats/` |

## Exporting to an open-science platform

`bids/` is the dataset. For an OpenNeuro upload you want the **raw** part —
`dataset_description.json`, `participants.tsv/.json`, and the `sub-*` folders —
and optionally `derivatives/`. You normally **exclude `sourcedata/conversion/`**,
which is your private working tree. `python code/export_openneuro.py` runs the
pre-flight checks, prints the validator command, and can assemble a slim
upload-ready copy under `deliverable/bids_openneuro/` with `--assemble`.
