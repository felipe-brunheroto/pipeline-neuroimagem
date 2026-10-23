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

The code is shared; each study has its own folder under `projects/`, and inside
it one BIDS root. Everything below `projects/<NAME>/` has the same shape for
every study.

```
Pipeline Neuroimagem/
├── code/                      # shared pipeline scripts + config (paths.py, series_map.csv, …)
├── Pipeline_Alunos/           # student teaching package (pt-BR guided menu; asks which study)
├── projects/
│   ├── CBR/                   # CBR / NEUROCOVID — input: DICOM
│   │   ├── project.json       # study settings (tracked in git)
│   │   ├── Patient Files/     # raw DICOM input — one folder per exam. READ-ONLY, never shared.
│   │   ├── bids/              # THE dataset for this study (see below)
│   │   ├── logs/batch/        # run_batch.py: per-run logs + summary.md/.tsv, state.json (kept local)
│   │   ├── archive/           # timestamped backups of previous runs (kept local)
│   │   └── deliverable/       # generated PDFs + handoff/export bundles (no patient data)
│   ├── COGAGING/              # input: DICOM from clinical CDs (+ legacy dcm2bids BIDS)
│   │   ├── project.json
│   │   ├── series_map.csv     # COGAGING scanner series rules (tracked in git)
│   │   ├── Patient Files/     # one folder per CD, name contains the code (GER00039_...). READ-ONLY.
│   │   ├── sourcedata/
│   │   │   ├── received/      # legacy BIDS from dcm2bids, untouched (READ-ONLY; site identifiers inside)
│   │   │   └── session_map.tsv    # exam date <-> ses-N (dates are identifiers: stays here, never in bids/)
│   │   ├── logs/batch/        # run_batch.py logs and summaries
│   │   └── bids/              # the de-identified dataset
│   └── .batch.lock            # machine-wide lock: one batch at a time (OS file lock)
├── tests/                     # self-tests on synthetic data (no data committed)
├── docs/                      # project documents (summary .docx, notes)
├── README.md  QUICKSTART.md  CLAUDE.md  STRUCTURE.md  requirements.txt
```

Inside each `projects/<NAME>/bids/`:

```
bids/
├── dataset_description.json, README, CHANGES, participants.tsv/.json, .bidsignore
├── sub-CBR003/ … (or sub-GER00007/ …)   # raw BIDS (the OpenNeuro upload target)
│   └── ses-1/ … /anat/  func/  dwi/
├── sourcedata/
│   ├── conversion/         # DICOM projects: working/pre-BIDS tree, provenance.csv,
│   │                       #   validation_report.csv, logs, reports/
│   ├── import/             # BIDS projects: import logs
│   └── asl/  fmap/  func/  unknown/   # partially-curated / held-for-review series
└── derivatives/
    ├── scanner-derived/    # ADC, TRACEW, SWI family, perfusion maps
    ├── defaced/            # face-masked anatomicals (desc-defaced)
    ├── freesurfer/         # recon-all output (+ stats/, logs/, .failed/ = last unfinished attempt)
    ├── samseg/             # SAMSEG lesions per session + samseg_lesions.tsv
    └── qc/                 # qc_status.tsv: visual QC pass/fail per session (student menu, item C)
```

> **code travels, data does not.** `code/`, `Pipeline_Alunos/`, the
> `project.json` files and the docs are safe to share/version-control.
> `Patient Files/`, `sourcedata/`, `bids/`, `archive/` and `logs/` are
> patient-derived and are git-ignored — they stay on the secure machine.

## Which step writes where

Run scripts as `python code/<script>.py --project NAME` from the repository
root. Defaults come from [`code/paths.py`](code/paths.py); no path arguments are
needed. Paths in this table are relative to `projects/<NAME>/`.

| # | Step (script) | Reads | Writes |
|---|---------------|-------|--------|
| 1b | `import_bids.py` (subjects delivered already in BIDS) | `sourcedata/received/` | `bids/sub-*` (ses-N by date, de-identified) + `bids/derivatives/scanner-derived/` + dataset metadata |
| 1 | `dicom_to_bids.py` | `Patient Files/` | `bids/sourcedata/conversion/` (NIfTIs, `provenance.csv`, `validation_report.csv`, `reports/`) |
| 2 | `deidentify_sidecars.py` | `bids/sourcedata/conversion/` | same (scrubs `*.json` in place; writes `deidentification_log.txt`) |
| 3 | `make_bids.py` | `…/conversion/provenance.csv` | `bids/sub-*` (raw anat/func) + dataset metadata |
| 4 | `make_derivatives.py` | `…/conversion/` | `bids/derivatives/scanner-derived/` + `bids/sourcedata/{asl,fmap,unknown}/` |
| 5 | `deface_anat.py` | `bids/sub-*` | `bids/derivatives/defaced/` |
| 6 | `run_recon_long.py` | `bids/` (or `…/defaced`) | `bids/derivatives/freesurfer/` |
| 7 | `extract_fs_stats.py` | `bids/derivatives/freesurfer/` | `bids/derivatives/freesurfer/stats/` |
| 8 | `run_samseg.py` (COGAGING) | `bids/sub-*` (T1w + FLAIR) | `bids/derivatives/samseg/` + `samseg_lesions.tsv` |
| — | `export_openneuro.py` | `bids/` | validation report + (optional) `deliverable/bids_openneuro/` |
| B | `run_batch.py` (all of the above that apply, only what is pending) | everything above | the same outputs as the steps + `logs/batch/<run>/` (step logs, `summary.md`, `summary.tsv`), `logs/batch/state.json` |
| — | `install_schedule.py` | — | `~/Library/LaunchAgents/org.pipeline-neuroimagem.batch.<name>.plist` (nightly `run_batch.py`) |
| C | student menu, item C (`qc_status.py`) | `bids/derivatives/freesurfer/`, `…/samseg/` | `bids/derivatives/qc/qc_status.tsv` |
| S | student menu, item S | `…/conversion/reports/unknown_series_report.csv` | a validated row appended to `series_map.csv` (re-converted by the next batch) |

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
| SAMSEG lesions | `bids/derivatives/samseg/sub-CBR003_ses-*/` (COGAGING) |
| Visual QC decisions | `bids/derivatives/qc/qc_status.tsv` (rows where `subject=sub-CBR003`) |
| What the nightly batch did with it | `logs/batch/<run>/summary.md` |

## Exporting to an open-science platform

`bids/` is the dataset. For an OpenNeuro upload you want the **raw** part —
`dataset_description.json`, `participants.tsv/.json`, and the `sub-*` folders —
and optionally `derivatives/`. You normally **exclude `sourcedata/conversion/`**,
which is your private working tree. `python code/export_openneuro.py` runs the
pre-flight checks, prints the validator command, and can assemble a slim
upload-ready copy under `deliverable/bids_openneuro/` with `--assemble`.
