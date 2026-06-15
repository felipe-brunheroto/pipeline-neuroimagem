# START HERE — CBR / NEUROCOVID pipeline (quick guide)

This bundle contains **code and documentation only — no patient data.**
Move DICOMs / derived imaging only by an approved institutional method
(encrypted drive / secure share), **never** via personal cloud.

There are two machines:

| Machine | Role | Scripts used |
|---|---|---|
| **Conversion Mac** (Felipe) | DICOM → NIfTI → de-identify → BIDS | `dicom_to_bids.py`, `deidentify_sidecars.py`, `make_bids.py` |
| **Recon Mac** (M4 / 64 GB, FreeSurfer 8) | defacing + structural analysis | `deface_anat.py`, `run_recon_long.py` |

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

python3 check_environment.py        # should say GOOD TO GO
```

**1. Preview the recon plan** (needs no FreeSurfer — safe to run anywhere)
```bash
python3 run_recon_long.py --dry-run
```

**2. Deface the anatomicals** (writes `BIDS_output/derivatives/defaced/`, keeps originals)
```bash
python3 deface_anat.py
```

**3. Run the FreeSurfer longitudinal stream** (cross → within-subject base → long)
```bash
python3 run_recon_long.py \
    --input BIDS_output/derivatives/defaced \
    --subjects-dir ~/fs_subjects --jobs 4 --threads 4
```
Budget ~8 GB RAM and a few cores per job; `--jobs 4` is comfortable on 64 GB.
Completed runs are skipped automatically; per-run logs land in `~/fs_subjects/logs/`.

**4. QC, then extract metrics**
```bash
freeview ...                          # visual surface check; note the Euler number
asegstats2table  / aparcstats2table   # -> tidy per-region tables for stats
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
