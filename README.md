# Pipeline Neuroimagem — DICOM/BIDS → BIDS → FreeSurfer, for several studies

A small, **local-only** pipeline that converts raw DICOM MRI into NIfTI,
organizes it into a working layout (`anat / func / dwi / fmap / perf / swi /
misc / unknown`), records provenance, validates key fields, de-identifies the
JSON sidecars to an Open-Science standard, and curates a validator-clean BIDS
dataset (`bids/`) with proper derivatives and sourcedata. Unfamiliar series
(e.g. a future BOLD project) are classified via an editable `series_map.csv`,
and anything unrecognized is quarantined and reported rather than mis-sorted.

**One shared engine, independent studies.** The scripts live in `code/`; each
study keeps its data in `projects/<NAME>/` with a `project.json` describing it.
The studies do not depend on each other: none inherits another's series rules,
protocol facts, outputs, logs or QC. What belongs to which study is decided by
the code in each name (`code_pattern` in project.json: `CBR-003…` → CBR,
`GER00039_…` → COGAGING), so the plug-and-play front door never needs to be told.

| Project | Input | Notes |
|---|---|---|
| `CBR` (CBR / NEUROCOVID) | DICOM (`projects/CBR/Patient Files/`) | longitudinal structural MRI, SWI, clinical DWI, multi-TI PASL in some exams |
| `COGAGING` | DICOM from clinical CDs (`projects/COGAGING/Patient Files/`, folder names carry the code, e.g. `GER00039_...`); subjects already converted with dcm2bids enter via `import_bids.py` | multi-vendor; 3D T1w + FLAIR, 64-dir DWI, resting-state BOLD; sessions numbered by exam date; SAMSEG lesions; no defacing yet |

Per-study settings live in `projects/<NAME>/project.json` (input, code pattern,
session numbering, recon-all flags, which steps apply, and facts about its own
protocol, e.g. CBR's multi-TI PASL without M0 and phase-only field map) and
`projects/<NAME>/series_map.csv` (that study's scanner series names, checked
before the shared `code/series_map.csv`, which holds no study's rules).

> **New Mac, new data? Start with [QUICKSTART.md](QUICKSTART.md):** one
> `git clone` and `start.command` set everything up, import DICOM CDs, dcm2bids
> BIDS, finished FreeSurfer runs or an old copy of the pipeline into the right
> study, and process them. After that, new data is just dropped in `inbox/`.

Every command takes **`--project NAME`** (or set `PIPELINE_PROJECT`), e.g.
`python code/run_recon_long.py --project COGAGING --dry-run`. Paths written as
`bids/...` below are relative to that project's folder (`projects/<NAME>/bids/...`).
**Each study lives under one BIDS root** (raw `sub-*`, the working conversion
tree under `sourcedata/conversion/`, and all `derivatives/`), so each is one
self-contained, OpenNeuro-ready folder. See **[STRUCTURE.md](STRUCTURE.md)** for
the full layout, a step→folder map, and how to trace one patient. The examples
below use `--project CBR`; swap in your project's name.

> **Golden rule: code travels, data does not.** The scripts in `code/` contain
> no patient data and can be shared/version-controlled freely. Raw DICOMs and the
> `bids/` dataset (which includes the working conversion tree) are patient-derived
> and must stay on the secure research machine — never on personal cloud storage
> (OneDrive/Dropbox/Drive).

---

## Files in this pipeline (safe to transfer)

All scripts live in **`code/`** (run them as `python code/<script>.py`).
`code/paths.py` is the single source of truth for the project layout, so no
path-editing is needed when moving the project.

| File (under `code/`) | What it is |
|------|------------|
| `paths.py` | One place that defines the layout (`PATIENT_FILES`, `BIDS_ROOT`, `CONVERSION_DIR`, `FREESURFER_DIR`, …) for the project chosen with `--project`. Imported by the other scripts. |
| `check_environment.py` | Verifies Python, dcm2niix, pydicom are installed, **and creates the `Patient Files/` input folder** (with a README) on first run so data is dropped in the right place. |
| `diagnostico.py` | **Guided check of this Mac, in Portuguese, for students**: Python/.venv, dcm2niix, FreeSurfer (native arm64) and its license, free disk, FileVault, project folder outside the macOS-protected folders, the nightly launchd job (points at THIS copy? loaded? wake schedule?), the last run of each study, and code updates. Each item says why it matters and how to fix it; safe fixes (.venv, license copy, reinstalling the schedule) only on a per-item "s". Counts only: never a patient or exam folder name; never sudo. `--salvar` writes the report to `logs/diagnostico/`. Double-click: `Pipeline_Alunos/diagnostico.command`; menu item 0. |
| `setup_workspace.py` | Creates/validates the `Patient Files/` drop folder and diagnoses common mistakes (pointing inside one exam, empty folder). Imported by the two above; also runnable directly. |
| `series_classifier.py` | Shared rules that decide each series' datatype + BIDS suffix. Reads `series_map.csv` first, then built-in keywords; unrecognized → `unknown`. |
| `series_map.csv` | **Operator-editable** map: teaches the pipeline new series (e.g. a future BOLD study) without code changes. |
| `dicom_to_bids.py` | Main pipeline: DICOM → NIfTI, sorts to anat/func/dwi/fmap/perf/swi/misc, writes provenance + validation + an **unknown-series report** and a readiness summary. |
| `import_bids.py` | For subjects delivered **already in BIDS** (COGAGING subjects converted with dcm2bids): copies the delivered `sub-*` folders into `bids/`, numbers visits by date order (`ses-YYYYMM` or no session → `ses-1`, `ses-2`…), removes identifier fields from every sidecar, moves scanner-derived images (e.g. TRACEW) to `derivatives/scanner-derived/`, writes the dataset files and checks the result. The delivered copy is never modified. |
| `session_map.py` | Visit numbering by exam date for projects with `"sessions": "date_order"`. The date → number map stays in `projects/<NAME>/sourcedata/session_map.tsv`, outside `bids/`; numbers never change, and a new exam older than a numbered one stops the run. |
| `run_samseg.py` | White-matter lesions with FreeSurfer SAMSEG (T1w + FLAIR registered to T1, `--lesion`, threshold 0.3) into `bids/derivatives/samseg/`, plus `samseg_lesions.tsv` (mm³ and % of intracranial). Needs a native arm64 FreeSurfer (stops under Rosetta). `--dry-run` needs no FreeSurfer. |
| `deidentify_sidecars.py` | Post-step: strips site/scanner identifiers from JSON sidecars (DICOM PS3.15 Basic Confidentiality Profile). |
| `make_bids.py` | Builds the validator-clean, FAIR raw BIDS dataset under `bids/`: anatomicals (`T1w`/`T2w`/`FLAIR`) and functional (`bold`, when a task is known). `acq-` preserves the protocol; incomplete BOLD is held under `sourcedata/func/`. |
| `make_derivatives.py` | Builds `bids/derivatives/scanner-derived/` (ADC, TRACEW, SWI, perfusion maps) and the `bids/sourcedata/` scaffolds for raw ASL (partially filled from the DICOMs) and the phase-only field map, plus quarantined `unknown` series. |
| `deface_anat.py` | Defaces the anatomicals (T1/T2/FLAIR) with FreeSurfer `mideface`, reading `bids/` and writing `bids/derivatives/defaced/` with `desc-defaced` names + a derivatives `dataset_description.json`. Needs FreeSurfer 7.3+. Run AFTER `make_bids.py`. |
| `run_recon_long.py` | Analysis driver (runs on the **FreeSurfer 8 Mac**): plans + runs the longitudinal stream (cross → base → long) into `bids/derivatives/freesurfer/`. `--dry-run` needs no FreeSurfer. |
| `extract_fs_stats.py` | Final step: wraps `asegstats2table`/`aparcstats2table` into tidy tables under `bids/derivatives/freesurfer/stats/`. `--dry-run` prints the plan. |
| `run_batch.py` | **Unattended processing** (processing Mac, nightly): runs every step that applies to the project, in order, only for new or unfinished work; safe to re-run at any time. Lock, per-run logs and an end-of-run summary in `projects/<NAME>/logs/batch/`. See [Unattended batch](#unattended-batch-processing-mac). |
| `install_schedule.py` | Installs/removes the nightly launchd job (`~/Library/LaunchAgents/…plist`): `--all` (what `pipeline.py schedule` uses) runs every study, `--project NAME` one; `--watch` also runs it when a folder is added. |
| `pipeline.py` | **Plug-and-play front door, all studies at once**: `import` (recognises DICOM CDs, dcm2bids `sub-*`, FreeSurfer runs and old pipeline copies anywhere in the folders given, and routes each by its study code), `run` (inbox, then every study's batch), `schedule`, `status`. |
| `import_inputs.py` | Places one kind of item into one study (via `paths.py`): never overwrites, never deletes a source, never guesses; FreeSurfer runs are renamed to `sub-X_ses-N` and kept apart when made by another FreeSurfer version. |
| `../start.command` | Double-click entry point: update from GitHub, `.venv`, FreeSurfer, import, process, schedule (see QUICKSTART). |
| `qc_status.py` | The visual-QC record (`bids/derivatives/qc/qc_status.tsv`): what is finished but not reviewed, the freeview command for it, and pass/fail decisions. Used by the batch summary and the student menu. |
| `batch_lock.py` | The machine-wide lock (an OS file lock, released even by a crashed run) that keeps two batches from overlapping. |
| `Pipeline_Alunos/` | **Student teaching package** (Brazilian Portuguese, non-programmer-friendly): double-click `iniciar.command` → guided menu (`passos.py`) whose first items are the work that needs a person — R: last batch summary in plain Portuguese, C: visual QC in freeview with pass/fail recorded, S: teach an unknown series (validated `series_map.csv` row) — followed by the numbered steps for one-off runs; `GUIA_DO_ALUNO.pdf` walkthrough; FAIR/safety notes. Runs with no Claude account. |
| `tests/` | Self-tests on synthetic data only (fake DICOMs written with pydicom at test time, stub FreeSurfer commands): `python -m pip install pytest` then `python -m pytest tests -q`. Never point them at real data. |
| `requirements.txt` | Python dependencies, pinned to validated versions. |
| `CLAUDE.md` | Safety/ethics rules for the project. |
| `.gitignore` | Safety net: blocks git from ever tracking patient data. |
| `README.md` | This file. |

**Never transfer via personal/cloud means:** the `CBR-*` subject folders
(`Patient Files/`) and the `bids/` dataset (it contains patient-derived imaging,
including the working conversion tree under `sourcedata/conversion/`).

---

## For collaborators (working on the code through GitHub)

The GitHub repository holds **code, settings and documents only**. Each author
clones it; the data stays on the lab Macs and is copied only by the lab's
approved methods (never through GitHub, cloud drives or chat).

```bash
git clone https://github.com/felipe-brunheroto/pipeline-neuroimagem.git
cd pipeline-neuroimagem
python3 -m pip install -r requirements.txt pytest
python3 -m pytest tests -q          # synthetic data only; ~1 min, all must pass
```

- **Change code on a branch and open a pull request**; another author reviews
  before it goes into `main`. Run `python3 -m pytest tests -q` before pushing.
  A change to shared code must keep `tests/test_cbr_unchanged.py` green (CBR
  outputs byte-identical).
- **Never commit data.** `.gitignore` blocks `Patient Files/`, `bids/`,
  `sourcedata/`, `logs/`, `archive/`, DICOM/NIfTI/MGZ files and the menu's
  per-machine choices; do not force-add (`git add -f`) anything it blocks, and
  check `git status` before every commit. Series rules added from the menu
  (`projects/<NAME>/series_map.csv`) are code-like settings and *are* committed.
- **Tests use synthetic data only** (`tests/synthetic.py` writes fake DICOMs at
  test time under a temporary `PIPELINE_PROJECTS_DIR`); never point a test at
  a real projects folder.
- **AI assistants** (Claude Code etc.) working in a clone must follow
  [`CLAUDE.md`](CLAUDE.md); on a machine with patient data, never in an
  auto-accept mode.
- **Updating a lab Mac:** `git pull` in its clone (the data folders are
  untouched). A runtime-only package for a Mac without git:
  `git archive --format=zip -o ~/Desktop/pipeline.zip HEAD` (see `.gitattributes`).

---

## Setup on a new (workplace) computer — Windows

1. **Choose a local-only folder, NOT inside OneDrive/Dropbox/Google Drive.**
   Example: `C:\Research\CBR\`. Copy the project into it (keep the `code/`
   folder intact — the scripts import each other and `paths.py`).

2. **Install Python 3.12** (once):
   ```powershell
   winget install --id Python.Python.3.12 -e --source winget
   ```
   Close and reopen the terminal so `python` is on PATH.

3. **Install the Python dependencies:**
   ```powershell
   python -m pip install -r requirements.txt
   ```
   (The `dcm2niix` pip package bundles the converter binary. If your site
   prefers a managed install, use `conda install -c conda-forge dcm2niix`
   instead and drop dcm2niix from `requirements.txt`.)

4. **Verify the environment:**
   ```powershell
   python code\check_environment.py
   ```
   Confirm it prints **GOOD TO GO**.

5. **Place the patient DICOM data locally**, one folder per subject
   (e.g. `C:\Research\CBR\Patient Files\CBR-003\`), transferred by an approved
   institutional method (encrypted drive / secure share) — not personal cloud.

6. **No path-editing needed.** Defaults come from `code/paths.py`
   (`Patient Files/` in, `bids/` out). To use a different location, pass paths on
   the command line — see below.

---

## Running the pipeline

Paths default (via `code/paths.py`) to `Patient Files/` (input) and the `bids/`
root (output; the working conversion tree lands in `bids/sourcedata/conversion/`),
so on the primary machine you can run them with no arguments. Run from the project
root. Explicit paths still work as an override:

```bash
# 1. Convert + organize + validate -> bids/sourcedata/conversion/
#    (also writes reports/unknown_series_report.csv there)
python code/dicom_to_bids.py --project CBR

# 2. De-identify the JSON sidecars (site/scanner fields)
python code/deidentify_sidecars.py --project CBR

# 3. Build the FAIR, validator-clean raw BIDS dataset (anat + func) -> bids/sub-*
python code/make_bids.py --project CBR

# 4. Build derivatives + sourcedata: scanner-derived maps (ADC/TRACEW/SWI/
#    perfusion) under bids/derivatives/scanner-derived/, and ASL + phase-only
#    field map scaffolds under bids/sourcedata/.
python code/make_derivatives.py --project CBR

# 5. Deface the anatomicals (T1/T2/FLAIR) — needs FreeSurfer 7.3+ (mideface).
#    Reads bids/, writes bids/derivatives/defaced/ with desc-defaced names.
python code/deface_anat.py --project CBR --dry-run    # preview (no FreeSurfer needed)
python code/deface_anat.py --project CBR              # actually deface
```

Re-running steps 3–4 is safe: each rebuilds only the subtree it owns and leaves
the FreeSurfer recon, defaced copies, and conversion source untouched.

**New project / unfamiliar series (e.g. BOLD):** the pipeline never crashes on
data it doesn't recognize. Any series no rule matches is converted, parked in
`bids/sourcedata/unknown/`, and listed in `reports/unknown_series_report.csv`
with the DICOM hints needed to classify it; ready-to-edit rows are written to
`reports/series_map_additions.csv`. Fill in the `datatype`/`suffix`, paste the
rows into `series_map.csv`, and re-run — no code changes. BOLD runs are detected
automatically (by name or DICOM `ImageType`); resting-state becomes
`task-rest_bold`, and task runs are held under `sourcedata/func/` until you set
`TaskName`.

To verify `bids/` is BIDS-valid, run the official validator on a machine that
has it (e.g. the processing Mac): `deno run -A jsr:@bids/validator bids`,
or `npx @bids/validator bids`. A quick local sanity check: `pip install pybids`
then `python -c "from bids import BIDSLayout; print(BIDSLayout('bids'))"`.
The data never leaves the machine in any case.

---

### COGAGING

Same steps 1–4 with `--project COGAGING`. Each CD goes in its own folder in
`projects/COGAGING/Patient Files/`, and the folder name must contain the code
(`GER00039_...`); nothing else from the folder name is written anywhere.
Sessions are numbered by exam date. No defacing for now (study decision).
Then:

```bash
python code/run_recon_long.py --project COGAGING --dry-run   # recon-all -all -qcache -3T (Cog-Aging guide 3.2)
python code/run_recon_long.py --project COGAGING --jobs 2
python code/run_samseg.py --project COGAGING                 # lesions (guide section 4)
python code/extract_fs_stats.py --project COGAGING
```

Subjects already converted with dcm2bids: put their `sub-*` folders in
`projects/COGAGING/sourcedata/received/` (read-only from then on) and run
`python code/import_bids.py --project COGAGING`. Re-running skips subjects
already in `bids/` (`--force` rebuilds). `run_recon_long.py` refuses to mix
FreeSurfer versions in one SUBJECTS_DIR (`--allow-mixed-versions` overrides).

## Structural analysis (FreeSurfer 8 — on the processing Mac)

The whole chain runs on **one processing Mac** (M4 Pro / 64 GB, **FreeSurfer 8**
native Apple-Silicon — no Rosetta; free license in `$FREESURFER_HOME/license.txt`):
the raw, identified DICOM is copied to it once, and conversion, de-identification,
BIDS, defacing, recon and tables all run there, answering only to what happens on
that machine. Nothing moves between computers mid-chain. `code/diagnostico.py`
checks that Mac (see the table above).

Workflow:

1. **Deface.** `deface_anat.py` (the nightly batch runs it when `steps.deface` is
   on). Recon can read the defaced tree.
2. **Source FreeSurfer 8**, then plan and run the longitudinal stream:
   ```bash
   export FREESURFER_HOME=/Applications/freesurfer/8.0.0
   source $FREESURFER_HOME/SetUpFreeSurfer.sh

   python code/run_recon_long.py --project CBR --dry-run                  # preview the plan
   python code/run_recon_long.py --project CBR --input projects/CBR/bids/derivatives/defaced \
       --jobs 4 --threads 4                                 # cross -> base -> long
   ```
   Output lands in `bids/derivatives/freesurfer/` (a declared BIDS derivative);
   pass `--input projects/CBR/bids/derivatives/defaced` to recon the privacy-preserving tree.
   ~8 GB RAM + `threads` cores per job; `--jobs 4` is comfortable on 64 GB.
3. **QC** the surfaces (freeview; check the Euler number) before trusting any
   subject.
4. **Extract metrics** with `python code/extract_fs_stats.py --project CBR` (wraps
   `asegstats2table` / `aparcstats2table` into `bids/derivatives/freesurfer/stats/`),
   then fit mixed-effects models (region ~ clinical_status × time + (1│subject)).
   With N=4 this is a pilot — favour hypothesis-driven ROIs over whole-brain
   vertexwise.

Then review, in `bids/sourcedata/conversion/`:
- `validation_report.csv` — any `MISMATCH` row should be checked before trusting that scan.
- `provenance.csv` — links each NIfTI back to its DICOM series.
- `deidentification_log.txt` — what the de-id step removed.
- `conversion_log.txt` — full run log.

**Treat steps 1 + 2 as one pipeline.** Data is not "shareable/de-identified"
until step 2 has run and an audit shows zero identifying fields.

---

## Unattended batch (processing Mac)

With ~74 COGAGING subjects arriving over time, nobody should have to sit in
front of recon-all. `run_batch.py` runs **everything that is pending, in
order, and nothing else**, and is safe to re-run at any time:

```bash
python3 code/run_batch.py --project COGAGING --dry-run   # what would run (changes nothing)
python3 code/run_batch.py --project COGAGING             # run it now (FreeSurfer loaded)
python3 code/pipeline.py run                             # inbox/, then every study in turn
python3 code/pipeline.py schedule --at 22:30             # every night, every study (one job)
python3 code/pipeline.py schedule --at 22:30 --watch     # + when data is added
python3 code/install_schedule.py --all --uninstall
```

- **Steps** (those that apply per `project.json`): import (`bids_import`) →
  convert (`dicom_to_bids.py --only-new`) → deidentify → make_bids →
  make_derivatives → deface (`steps.deface`) → SAMSEG (`steps.samseg`) →
  recon (cross → base → long) → FreeSurfer tables. SAMSEG does not wait for
  recon. A failed step blocks only what needs its output (a failed
  de-identification blocks BIDS; a session-order stop on one new CD does not
  stop the recon of exams already in `bids/`).
- **Only new or unfinished work.** What is pending is read from the outputs
  every run (`provenance.csv`, `recon-all.done`, `samseg.stats`, …), so a
  crash or a stop just resumes next run; a second run with nothing new changes
  nothing. New CD folders in `Patient Files/` are picked up by the next run; a
  folder changed in the last 30 min (`--settle-minutes`) is left for the next
  one (still being copied). After a rule is added for an unknown series, the
  exams that had it are converted again automatically.
- **Retries.** A failed item is retried on the next run. An unfinished
  recon-all folder is moved to `bids/derivatives/freesurfer/.failed/` (latest
  attempt kept) and restarted, because recon-all refuses `-i` on an existing
  subject. After `--max-attempts` failures (default 3) a recon/SAMSEG run is
  left out and listed for a person; `--retry-failed` tries again.
- **One at a time.** A machine-wide lock (`projects/.batch.lock`) refuses a
  second run (`--wait MINUTES` queues instead; the nightly job uses 12 h).
  The studies share nothing but the machine's cores, memory and disk, which
  is why their batches take turns.
- **Refuses to start** when FreeSurfer is not loaded or has no license
  (`--no-freesurfer` runs only the conversion/BIDS steps), when SAMSEG would run
  on an Intel FreeSurfer under Rosetta, or with less than `--min-free-gb`
  (default 50) free. The batch always passes `--subjects-dir
  bids/derivatives/freesurfer`, so the `SUBJECTS_DIR` set by
  `SetUpFreeSurfer.sh` is never used by mistake.
- **Parallelism (64 GB).** recon-all: `--threads 2` and as many jobs as the
  performance cores and ~8 GB per job allow (4–5 on the M4 Pro); SAMSEG: 4
  threads, ~12 GB per job. Override with `--jobs/--threads/--samseg-jobs/--samseg-threads`.
- **Awake only while running:** `caffeinate -is -w <pid>` for the run's
  lifetime. A sleeping Mac runs a missed nightly job when it wakes; to wake it,
  `sudo pmset repeat wakeorpoweron MTWRFSU 22:25:00` (printed by the installer).
- **Logs and summary** in `projects/<NAME>/logs/batch/` (git-ignored, kept
  local): `<run>/batch.log`, one `NN_<step>.log` per step, `summary.md` +
  `summary.tsv` (what ran, what was skipped, what failed and why, and what
  needs a person: unknown series, MISMATCH rows, session-order stops, items that
  gave up, QC failures, QC to do), `last_summary.md/.tsv`, `state.json`,
  `launchd.log`. Only study codes appear; for COGAGING every piece of an exam
  folder name (where a patient name may be) is masked in the step logs.
- Exit code: 0 done (items for a person may remain), 1 a step failed, 2 refused,
  3 another batch is running.

**Batch or menu?** The batch is for the long, repetitive work on the processing Mac
(conversion, BIDS, SAMSEG, recon, tables), nightly and unattended. The student
menu (`Pipeline_Alunos/iniciar.command`) is for what needs a person: reading
the last summary (item R), visual QC in freeview with pass/fail recorded in
`bids/derivatives/qc/qc_status.tsv` (item C), and teaching the pipeline an
unknown series by adding a validated row to `projects/<NAME>/series_map.csv`
(item S). Its numbered steps remain for one-off runs when asked, and refuse to
start while a batch is running.

---

## Known caveats

- **Diffusion `*_TRACEW` TR mismatch** in `validation_report.csv` is benign
  (dcm2niix issue #560 — it writes the estimated multi-volume TR). Image data is
  unaffected.
- **Routing & new series**: routing rules live in `series_map.csv` +
  `series_classifier.py` (no longer hardcoded in `dicom_to_bids.py`). Localizers
  (`AAHead_Scout`) land in `misc/`; anything unrecognized goes to `unknown/` and
  is reported for you to classify via `series_map.csv`.
- **Diffusion is derived only**: this protocol exports `TRACEW` + `ADC` (no
  bval/bvec), so they are curated as **derivatives** under
  `derivatives/scanner-derived/`, not raw `dwi/`.
- **ASL**: raw PASL is partially curated under `sourcedata/asl/` —
  `PostLabelingDelay` (from DICOM `InversionTime`), `M0Type:Absent`, and bolus
  cut-off fields (from `PASLType`/`BolusDuration`) are filled and annotated;
  `BackgroundSuppression`, `TotalAcquiredPairs`, and the per-volume
  `aslcontext.tsv` order must be confirmed by the researcher.
- **Field map**: only the **phase** image was acquired (no magnitude), so a valid
  BIDS fieldmap is impossible; it is kept under `sourcedata/fmap/` with a README.
- **Defacing**: run `deface_anat.py` (FreeSurfer `mideface`) **after**
  `make_bids.py`. Defaced copies go to `bids/derivatives/defaced/` with
  `desc-defaced` names; the originals are never modified. Deface every timepoint
  with the same tool to keep longitudinal comparisons consistent.
