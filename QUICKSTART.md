# START HERE — Pipeline Neuroimagem (quick guide)

This bundle contains **code and documentation only — no patient data.**
Move DICOMs / derived imaging only by an approved institutional method
(encrypted drive / secure share), **never** via personal cloud.

There are two machines:

| Machine | Role | Scripts used |
|---|---|---|
| **Conversion Mac** (Felipe) | DICOM → NIfTI → de-identify → BIDS + derivatives | `dicom_to_bids.py`, `deidentify_sidecars.py`, `make_bids.py`, `make_derivatives.py` |
| **Recon Mac** (M4 / 64 GB, FreeSurfer 8) | defacing + structural analysis | `deface_anat.py`, `run_recon_long.py`, `extract_fs_stats.py` |

The full PDF (`CBR_NEUROCOVID_pipeline_overview.pdf`) is the 2-page summary to
read first. `README.md` has the complete reference.

---

## Recon Mac — what to run (FreeSurfer 8, native Apple Silicon)

**0. One-time setup**
```bash
# Python deps (dcm2niix wheel is bundled; pydicom for tags)
python3 -m pip install -r requirements.txt

# Install FreeSurfer 8 (macOS arm64 build) and put your free license at:
#   $FREESURFER_HOME/license.txt     (register at surfer.nmr.mgh.harvard.edu/registration.html)
export FREESURFER_HOME=/Applications/freesurfer/8.0.0   # adjust to your install
source $FREESURFER_HOME/SetUpFreeSurfer.sh

python3 code/check_environment.py --project CBR   # should say GOOD TO GO
```

(All scripts live in `code/`; run them from the repository root with
`--project CBR` or `--project COGAGING`. Each study's data lives in
`projects/<NAME>/`, under one `bids/` root — see `STRUCTURE.md`. The commands
below are for CBR; COGAGING uses the same commands with `--project COGAGING`
(no defacing for now), plus `python3 code/run_samseg.py --project COGAGING`
for white-matter lesions. Legacy dcm2bids subjects:
`python3 code/import_bids.py --project COGAGING`.)

**1. Build the BIDS tree** (after `code/dicom_to_bids.py` + `code/deidentify_sidecars.py`)
```bash
python3 code/make_bids.py --project CBR            # raw anat (+func) -> bids/sub-*
python3 code/make_derivatives.py --project CBR     # scanner-derived maps + ASL/fmap sourcedata
python3 code/run_recon_long.py --project CBR --dry-run   # preview the recon plan (no FreeSurfer)
```

**2. Deface the anatomicals** (reads `bids/`, writes `bids/derivatives/defaced/`, keeps originals)
```bash
python3 code/deface_anat.py --project CBR
```

**3. Run the FreeSurfer longitudinal stream** (cross → within-subject base → long)
```bash
python3 code/run_recon_long.py --project CBR --input projects/CBR/bids/derivatives/defaced \
    --jobs 4 --threads 4
```
Output lands in `bids/derivatives/freesurfer/` (a declared BIDS derivative).
Budget ~8 GB RAM and a few cores per job; `--jobs 4` is comfortable on 64 GB.
Completed runs are skipped automatically; per-run logs land in
`bids/derivatives/freesurfer/logs/`.

**4. QC, then extract metrics**
```bash
freeview ...                          # visual surface check; note the Euler number
python3 code/extract_fs_stats.py --project CBR      # -> tidy tables in bids/derivatives/freesurfer/stats/
```

---

## Notes
- For this dataset the plan is **6 cross-sectional + 2 within-subject templates
  (CBR003, CBR011) + 4 longitudinal** runs. Single-visit subjects (CBR052,
  CBR053) get the cross-sectional run only and join the longitudinal stream when
  follow-ups arrive.
- Sessions follow the study scheme: **ses-1 = baseline, ses-2 = 6-month,
  ses-3 = 12-month.**
- N=4 is a **pilot** — plan hypothesis-driven ROI analysis with mixed-effects
  models, not whole-brain vertexwise.
- Validate the BIDS tree on this Mac: `deno run -A jsr:@bids/validator bids`.
