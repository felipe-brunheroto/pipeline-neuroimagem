# START HERE — Pipeline Neuroimagem (quick guide)

This repository contains **code and documentation only — no patient data.**
Move DICOMs / derived imaging only by an approved institutional method
(encrypted drive / secure share), **never** via GitHub or personal cloud.

One pipeline serves **two independent studies**, CBR (CBR / NEUROCOVID) and
COGAGING. Neither depends on the other: each has its own folder under
`projects/`, its own series rules, protocol facts, outputs, logs and QC. What
belongs to which study is decided by the code in each name: `CBR-003…` → CBR,
`GER00039_…` → COGAGING.

---

## Plug and play (a new Mac, or after any update)

Once, in Terminal (the repository is private: git asks you to sign in to
GitHub; use a personal access token or `gh auth login`):

```bash
git clone https://github.com/felipe-brunheroto/pipeline-neuroimagem.git ~/pipeline-neuroimagem
bash ~/pipeline-neuroimagem/start.command
```

After that, double-click `start.command` (or keep using the line above). Each
time it:

1. checks GitHub for new code, lists the new commits and updates only if you
   say yes (`--yes` never updates: code is never run unseen on a Mac that holds
   patient files; data folders are never in git);
2. prepares Python in `.venv/` (needs Python 3.10+, e.g. from python.org;
   the 3.9 that ships with macOS is too old for pydicom 3);
3. loads FreeSurfer (`$FREESURFER_HOME`, else the newest `/Applications/freesurfer/*`);
4. asks you to drag in folders to import, shows the plan, and imports on your
   OK: **DICOM CDs, dcm2bids BIDS subjects, finished FreeSurfer runs, or a whole
   old copy of the pipeline** (any older layout). Each item goes to its own
   study (see `code/pipeline.py` / `code/import_inputs.py`):
   - DICOM exam → `projects/<NAME>/Patient Files/`
   - dcm2bids `sub-*` → `projects/COGAGING/sourcedata/received/`
   - FreeSurfer run made by the FreeSurfer in use → `bids/derivatives/freesurfer/`
     (renamed `sub-X_ses-N`; the batch will not redo it); made by another
     version (e.g. 7.4.1) → `bids/derivatives/freesurfer-<version>/`, never mixed
   - an old `session_map.tsv` → kept, so COGAGING session numbers never change
   - nothing is overwritten or deleted; what cannot be placed safely (no code,
     a COGAGING FreeSurfer run without a session in its name, an unfinished run)
     is reported and left where it is. Dragged folders are **copied**.
5. processes every study now (or leaves it for the night);
6. offers to repeat 4–5 every night at 22:30 (one launchd job for all studies), and
   asks how many recon-all may run at once (ENTER = as many as fit; 2 when the Mac is
   shared with other heavy work, each recon-all takes ~8 GB).

Something looks wrong (nothing processed overnight, a step refuses to start)?
Double-click `Pipeline_Alunos/diagnostico.command`: it checks this Mac, explains
each item in Portuguese and offers the safe fixes.

From then on, new data only needs to be **dropped in `inbox/`** (next to
`projects/`): the next run moves each item into its study and processes it.

---

## By hand (the same steps, one at a time)

```bash
.venv/bin/python code/pipeline.py import /path/to/folder --dry-run   # what goes where
.venv/bin/python code/pipeline.py import /path/to/folder             # copy it in
.venv/bin/python code/pipeline.py run                                # every study's batch
.venv/bin/python code/pipeline.py status                             # what each study holds
.venv/bin/python code/run_batch.py --project COGAGING --dry-run      # one study's plan
```

Each run leaves `projects/<NAME>/logs/batch/last_summary.md` (what ran, what
failed and why, what needs a person, QC to do). Students read it, do the QC and
teach unknown series from their menu (`Pipeline_Alunos/iniciar.command`, items
R, C, S). On a Mac without FreeSurfer the batch runs conversion and BIDS only.
Details: README, "Plug and play" and "Unattended batch"; layout: STRUCTURE.md.

---

## Notes
- Sessions: CBR numbers visits by folder suffix (`CBR-003` = baseline ses-1,
  `CBR-003.2` = 6 months, `CBR-003.3` = 12 months); COGAGING numbers them by
  exam date, with the date map kept in `projects/COGAGING/sourcedata/session_map.tsv`.
- One FreeSurfer version per study: `run_recon_long.py` refuses to mix them in
  one SUBJECTS_DIR, and imported runs of another version stay apart.
- Validate a BIDS tree on the Mac: `deno run -A jsr:@bids/validator projects/<NAME>/bids`.
