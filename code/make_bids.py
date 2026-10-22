"""
make_bids.py
============

PURPOSE
-------
Turn the human-readable working/conversion tree (`bids/sourcedata/conversion/`)
into the validator-clean, FAIR raw BIDS that lives at the `bids/` root, so the
data can be fed to BIDS-apps (MRIQC, fMRIPrep/sMRIPrep, FastSurfer BIDS mode,
etc.) and uploaded to an open-science platform (OpenNeuro).

WHY KEEP THE CONVERSION TREE SEPARATE (not renaming in place)
-------------------------------------------------------------
  * `bids/sourcedata/conversion/` is never modified -> the conversion stays
    retrievable and keeps its descriptive scanner names for human comprehension.
  * The raw `bids/sub-*` imaging is regenerated from it -> fully reproducible.
  * The scanner protocol name is preserved in the BIDS `acq-` entity, so nothing
    is lost: e.g. `t1_mprage_sag_p2_iso` -> `..._acq-t1mpragesagp2iso_T1w.nii.gz`.

SCOPE (deliberately limited, and documented)
--------------------------------------------
Only the ANATOMICAL scans are curated to raw BIDS here (T1w / T2w / FLAIR) - that
is what the structural analysis needs and what BIDS-apps validate cleanly. The
diffusion (incl. derived TRACEW/ADC), fieldmap, ASL/perfusion and SWI series are
curated by make_derivatives.py into `derivatives/` and `sourcedata/` (ASL needs
aslcontext.tsv, fieldmaps need `IntendedFor`, etc.), rather than emitting invalid
names. This is stated in the generated README and dataset_description.json.

RE-RUN SAFETY
-------------
Re-running rebuilds ONLY the raw `bids/sub-*` imaging it owns (and its own
`sourcedata/func/`). It deliberately leaves `sourcedata/conversion`,
`sourcedata/asl|fmap|unknown` and everything under `derivatives/` (FreeSurfer,
defaced, scanner-derived) untouched, so adding a subject never destroys work
from later pipeline steps.

INPUTS / OUTPUTS
----------------
Reads `bids/sourcedata/conversion/provenance.csv` (the source of truth linking
each NIfTI to its series + category). Writes the raw BIDS at the `bids/` root:
    dataset_description.json, README, CHANGES, .bidsignore,
    participants.tsv (+ .json), sub-*/sub-*_sessions.tsv (+ sessions.json),
    sub-*/ses-*/anat/sub-*_ses-*_acq-*_{T1w,T2w,FLAIR}.nii.gz (+ .json)

HOW TO RUN
----------
    python code/make_bids.py            # conversion -> bids (defaults from paths.py)
    python code/make_bids.py /path/to/conversion /path/to/bids
"""

import os
import re
import csv
import sys
import json
import glob
import shutil
from datetime import datetime
from pathlib import Path

import paths


BIDS_VERSION = "1.9.0"

# Identifier fields that must never appear in a shared sidecar. deidentify_
# sidecars.py already removes these; we re-check here as defence in depth, since
# `bids/` is the tree most likely to be shared.
IDENTIFIER_FIELDS = [
    "InstitutionName", "InstitutionAddress", "InstitutionalDepartmentName",
    "StationName", "DeviceSerialNumber", "ProcedureStepDescription",
    "StudyDescription",
    "PatientName", "PatientID", "PatientBirthDate", "AccessionNumber",
]


def anat_suffix(description):
    """
    Map a series description to a BIDS anatomical suffix, or None if it is not a
    structural scan we curate. FLAIR is checked first because FLAIR protocols
    here are themselves T2-SPACE variants (e.g. 't2_space_da-fl3').
    """
    text = (description or "").lower()
    if any(k in text for k in ["flair", "fl3", "da-fl", "dark"]):
        return "FLAIR"
    if any(k in text for k in ["t1", "mprage", "mp2rage", "spgr", "bravo", "tfl"]):
        return "T1w"
    if any(k in text for k in ["t2", "space", "tse"]):
        return "T2w"
    return None


def acq_label(description):
    """Alphanumeric-only acquisition label that faithfully preserves the
    scanner protocol name (BIDS `acq-` allows [a-zA-Z0-9] only)."""
    return "".join(ch for ch in (description or "") if ch.isalnum())


def session_meta(session_label):
    """
    Map ses-N to the project's visit scheme:
        ses-1 -> baseline (0 months), ses-2 -> 6-month, ses-3 -> 12-month.
    Returns (visit_text, months_from_baseline).
    """
    m = re.search(r"(\d+)", session_label or "")
    idx = int(m.group(1)) if m else 1
    months = (idx - 1) * 6
    visit = {0: "baseline", 6: "6-month", 12: "12-month"}.get(months, f"{months}-month")
    return visit, months


def _parse_entities(text):
    """Parse a provenance 'bids_entities' cell ('k=v;k=v') into a dict."""
    out = {}
    for piece in (text or "").split(";"):
        piece = piece.strip()
        if "=" in piece:
            k, _, v = piece.partition("=")
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def load_provenance(output_dir):
    """Read provenance.csv (the source of truth) into a list of row dicts."""
    prov = os.path.join(output_dir, "provenance.csv")
    if not os.path.isfile(prov):
        print(f"ERROR: {prov} not found. Run dicom_to_bids.py first.")
        sys.exit(1)
    with open(prov, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_anat_rows(output_dir, rows=None):
    """Return only the anatomical rows we can map (prefer the suffix already
    decided at conversion time; fall back to anat_suffix for older provenance)."""
    rows = rows if rows is not None else load_provenance(output_dir)
    out = []
    for row in rows:
        if row.get("category") != "anat":
            continue
        suffix = row.get("bids_suffix") or anat_suffix(row.get("series_description"))
        if not suffix:
            continue
        row["_suffix"] = suffix
        out.append(row)
    return out


def safe_task_label(text):
    """BIDS task entity: alphanumeric only. '' -> '' (caller handles placeholder)."""
    return "".join(ch for ch in (text or "") if ch.isalnum())


def read_json_field(path, field, default=None):
    """Read one field from a JSON sidecar; missing file/field -> default."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get(field, default)
    except Exception:
        return default


def load_func_rows(output_dir, rows=None):
    """
    Return the FUNCTIONAL (BOLD) rows. We split them into:
      * 'ready'      : a task is known (e.g. resting-state) AND RepetitionTime is
                       present -> can be emitted as valid BIDS raw func.
      * 'incomplete' : task unknown (or no TR) -> held under sourcedata/func/ with
                       a clear placeholder until the researcher supplies TaskName,
                       so bids/ raw stays validator-clean.
    Derived func images (if any) are left for make_derivatives.py.
    """
    rows = rows if rows is not None else load_provenance(output_dir)
    ready, incomplete = [], []
    for row in rows:
        if row.get("category") != "func" or row.get("derived") == "yes":
            continue
        ent = _parse_entities(row.get("bids_entities"))
        task = safe_task_label(ent.get("task", ""))
        src_json = os.path.join(output_dir, row.get("output_json") or "")
        tr = read_json_field(src_json, "RepetitionTime")
        row["_task"] = task
        row["_tr"] = tr
        if task and tr:
            ready.append(row)
        else:
            incomplete.append(row)
    return ready, incomplete


def copy_sidecar(src_json, dest_json):
    """Copy a JSON sidecar, scrubbing any identifier field as defence in depth."""
    with open(src_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    for field in IDENTIFIER_FIELDS:
        data.pop(field, None)
    with open(dest_json, "w", encoding="utf-8") as f:
        json.dump(data, f, indent="\t", ensure_ascii=False)
        f.write("\n")


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent="\t", ensure_ascii=False)
        f.write("\n")


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build_dataset(output_dir, bids_dir):
    provenance = load_provenance(output_dir)
    rows = load_anat_rows(output_dir, provenance)
    func_ready, func_incomplete = load_func_rows(output_dir, provenance)
    if not rows and not func_ready and not func_incomplete:
        print("No anatomical or functional rows found in provenance.csv; "
              "nothing to do.")
        return

    # Fresh, deterministic output -- but SCOPED. We rebuild only the raw imaging
    # this script owns: the `sub-*` subject folders and our own `sourcedata/func/`.
    # We MUST NOT wipe the whole bids/ root, because it also holds work produced by
    # later steps -- sourcedata/conversion (our own input!), sourcedata/asl|fmap|
    # unknown, and everything under derivatives/ (scanner-derived, defaced,
    # freesurfer). A full rmtree here would silently destroy hours of recon every
    # time a subject is added. The top-level metadata files are overwritten below.
    os.makedirs(bids_dir, exist_ok=True)
    for sub_dir in glob.glob(os.path.join(bids_dir, "sub-*")):
        if os.path.isdir(sub_dir):
            shutil.rmtree(sub_dir)
    shutil.rmtree(os.path.join(bids_dir, "sourcedata", "func"), ignore_errors=True)

    subjects = {}              # participant_id -> set(session_label)
    n_copied = 0
    # Track (subject, session, suffix) to add run- entities if a scan repeats.
    seen = {}

    for row in sorted(rows, key=lambda r: (r["subject"], r["session"],
                                           r["_suffix"], r["series_number"])):
        subject = row["subject"]
        session = row["session"]
        suffix = row["_suffix"]
        acq = acq_label(row["series_description"])

        subjects.setdefault(subject, set()).add(session)

        key = (subject, session, suffix, acq)
        seen[key] = seen.get(key, 0) + 1
        run_entity = f"_run-{seen[key]:02d}" if seen[key] > 1 else ""

        anat_dir = os.path.join(bids_dir, subject, session, "anat")
        os.makedirs(anat_dir, exist_ok=True)
        stem = f"{subject}_{session}_acq-{acq}{run_entity}_{suffix}"

        src_nifti = os.path.join(output_dir, row["output_nifti"])
        src_json = os.path.join(output_dir, row["output_json"])
        if not os.path.isfile(src_nifti):
            print(f"  [warn] missing NIfTI, skipping: {row['output_nifti']}")
            continue
        shutil.copy2(src_nifti, os.path.join(anat_dir, stem + ".nii.gz"))
        if row["output_json"] and os.path.isfile(src_json):
            copy_sidecar(src_json, os.path.join(anat_dir, stem + ".json"))
        n_copied += 1
        print(f"  {subject}/{session}/anat/{stem}.nii.gz")

    # ----- functional (BOLD) -------------------------------------------------
    # Ready func (task known + RepetitionTime present) -> valid BIDS raw func.
    # Incomplete func -> sourcedata/func/ with a placeholder, so bids/ raw stays
    # validator-clean until the researcher confirms TaskName/events.
    n_func = 0
    func_tasks = set()
    seen_func = {}
    for row in sorted(func_ready, key=lambda r: (r["subject"], r["session"],
                                                 r["_task"], r["series_number"])):
        subject, session = row["subject"], row["session"]
        task, tr = row["_task"], row["_tr"]
        acq = acq_label(row["series_description"])
        key = (subject, session, task, acq)
        seen_func[key] = seen_func.get(key, 0) + 1
        run_entity = f"_run-{seen_func[key]:02d}" if seen_func[key] > 1 else ""
        func_dir = os.path.join(bids_dir, subject, session, "func")
        os.makedirs(func_dir, exist_ok=True)
        stem = f"{subject}_{session}_task-{task}_acq-{acq}{run_entity}_bold"
        src_nifti = os.path.join(output_dir, row["output_nifti"])
        src_json = os.path.join(output_dir, row.get("output_json") or "")
        if not os.path.isfile(src_nifti):
            print(f"  [warn] missing func NIfTI, skipping: {row['output_nifti']}")
            continue
        shutil.copy2(src_nifti, os.path.join(func_dir, stem + ".nii.gz"))
        # Sidecar MUST carry TaskName (required by BIDS for bold).
        if row.get("output_json") and os.path.isfile(src_json):
            with open(src_json, "r", encoding="utf-8") as f:
                sidecar = json.load(f)
        else:
            sidecar = {"RepetitionTime": tr}
        for field in IDENTIFIER_FIELDS:
            sidecar.pop(field, None)
        sidecar["TaskName"] = task
        write_json(os.path.join(func_dir, stem + ".json"), sidecar)
        subjects.setdefault(subject, set()).add(session)
        func_tasks.add(task)
        n_func += 1
        print(f"  {subject}/{session}/func/{stem}.nii.gz")

    # A top-level task json + events stub helps downstream tools; one per task.
    for task in sorted(func_tasks):
        events_stub = os.path.join(bids_dir, f"task-{task}_events.json")
        if not os.path.isfile(events_stub):
            write_json(events_stub, {
                "onset": {"Description": "Event onset (s).", "Units": "s"},
                "duration": {"Description": "Event duration (s).", "Units": "s"},
                "trial_type": {"Description": "Condition label."},
            })

    # Incomplete func: park under sourcedata/func/ with a clear to-do, never in
    # bids/ raw (where it would fail validation).
    if func_incomplete:
        sd_dir = os.path.join(bids_dir, "sourcedata", "func")
        os.makedirs(sd_dir, exist_ok=True)
        for row in func_incomplete:
            subject, session = row["subject"], row["session"]
            acq = acq_label(row["series_description"])
            dest = os.path.join(sd_dir, subject, session, "func")
            os.makedirs(dest, exist_ok=True)
            stem = f"{subject}_{session}_task-FILLME_acq-{acq}_bold"
            src_nifti = os.path.join(output_dir, row["output_nifti"])
            if os.path.isfile(src_nifti):
                shutil.copy2(src_nifti, os.path.join(dest, stem + ".nii.gz"))
            src_json = os.path.join(output_dir, row.get("output_json") or "")
            sidecar = {}
            if row.get("output_json") and os.path.isfile(src_json):
                with open(src_json, "r", encoding="utf-8") as f:
                    sidecar = json.load(f)
            for field in IDENTIFIER_FIELDS:
                sidecar.pop(field, None)
            sidecar["TaskName"] = "REQUIRED_FILL_ME"
            write_json(os.path.join(dest, stem + ".json"), sidecar)
        write_text(os.path.join(sd_dir, "README"),
            "Functional (BOLD) series held here pending researcher input.\n"
            "================================================================\n\n"
            "These runs were detected as BOLD but could not be emitted as valid\n"
            "BIDS raw func because the TASK is unknown (and/or RepetitionTime is\n"
            "missing). To promote them into bids/<sub>/<ses>/func/:\n"
            "  1. Decide the task label (e.g. 'rest', or your task name).\n"
            "  2. Set 'TaskName' in each .json (replace REQUIRED_FILL_ME).\n"
            "  3. Add a task-<label> rule to series_map.csv and re-run, or rename\n"
            "     the files to sub-XX_ses-YY_task-<label>[_run-NN]_bold.\n"
            "  4. For task designs, add a matching _events.tsv per run.\n")

    # ----- dataset-level metadata (the FAIR skeleton) ------------------------
    write_json(os.path.join(bids_dir, "dataset_description.json"), {
        "Name": f"{paths.PROJECT['title']} structural MRI",
        "BIDSVersion": BIDS_VERSION,
        "DatasetType": "raw",
        "GeneratedBy": [{
            "Name": "Pipeline Neuroimagem (dicom_to_bids.py + make_bids.py)",
            "Description": "dcm2niix conversion, de-identification, and BIDS "
                           "curation of anatomical scans.",
        }],
        "HowToAcknowledge": f"Please contact the {paths.PROJECT['title']} study team.",
    })

    write_text(os.path.join(bids_dir, "README"),
        f"{paths.PROJECT['title']} structural MRI (BIDS)\n"
        "======================================\n\n"
        "BIDS-valid dataset generated by make_bids.py from the project's\n"
        "DICOM->NIfTI pipeline. JSON sidecars are de-identified (DICOM PS3.15\n"
        "Basic Confidentiality Profile). Subject codes are study codes only.\n\n"
        "SCOPE\n"
        "-----\n"
        "Curated into raw BIDS: ANATOMICAL scans (T1w / T2w / FLAIR) and, when\n"
        "present, FUNCTIONAL (BOLD) runs with a known task. The scanner protocol\n"
        "name is preserved in the `acq-` entity.\n\n"
        "Sessions follow the study visit scheme:\n"
        "  ses-1 = baseline (0 months)\n"
        "  ses-2 = 6-month follow-up\n"
        "  ses-3 = 12-month follow-up\n"
        "(Subjects present only at later visits correctly start at ses-2/ses-3.)\n\n"
        "NOT in raw BIDS: scanner-derived maps (TRACEW/ADC, SWI family,\n"
        "Perfusion_Weighted) are curated under derivatives/scanner-derived/ by\n"
        "make_derivatives.py. Raw ASL and the phase-only fieldmap (magnitude not\n"
        "acquired) live under sourcedata/ with READMEs, pending parameters that\n"
        "are not in the DICOMs. BOLD runs with an unknown task are held under\n"
        "sourcedata/func/ until TaskName is supplied.\n\n"
        "Defaced anatomicals (FreeSurfer mideface) live under\n"
        "derivatives/defaced/ and can be substituted before sharing.\n")

    write_text(os.path.join(bids_dir, "CHANGES"),
        f"1.0.0 {datetime.now().strftime('%Y-%m-%d')}\n"
        "  - Initial BIDS release: anatomical scans (T1w/T2w/FLAIR), all visits.\n"
        "  - Functional (BOLD) curated when a task is known.\n")

    # Nothing non-conforming is written into bids/, so .bidsignore is empty but
    # present (a stub for future additions like logs).
    write_text(os.path.join(bids_dir, ".bidsignore"), "")

    # ----- participants.tsv / .json -----------------------------------------
    participant_ids = sorted(subjects)
    with open(os.path.join(bids_dir, "participants.tsv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["participant_id", "clinical_status"])
        for pid in participant_ids:
            w.writerow([pid, "n/a"])   # placeholder for the clinical correlate
    write_json(os.path.join(bids_dir, "participants.json"), {
        "clinical_status": {
            "Description": "Clinical status for the imaging-vs-clinical "
                           "correlation. Placeholder ('n/a') - fill from the "
                           "study clinical database. Do not enter free-text PHI.",
        },
    })

    # ----- per-subject sessions.tsv (the visit scheme) + shared sessions.json -
    for pid in participant_ids:
        sub_dir = os.path.join(bids_dir, pid)
        with open(os.path.join(sub_dir, f"{pid}_sessions.tsv"), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["session_id", "visit", "months_from_baseline"])
            for ses in sorted(subjects[pid]):
                visit, months = session_meta(ses)
                w.writerow([ses, visit, months])
    write_json(os.path.join(bids_dir, "sessions.json"), {
        "visit": {"Description": "Study visit label."},
        "months_from_baseline": {
            "Description": "Nominal months since the baseline visit "
                           "(0/6/12). Index-based, not an actual date, to keep "
                           "the dataset de-identified.",
            "Units": "months",
        },
    })

    print("")
    print("=" * 70)
    print(f" DONE. Wrote {n_copied} anatomical + {n_func} functional scan(s) for "
          f"{len(participant_ids)} subject(s) to:")
    print(f"   {bids_dir}")
    if func_incomplete:
        print(f" Held {len(func_incomplete)} BOLD run(s) with unknown task under "
              f"sourcedata/func/ (need TaskName).")
    print(" Subjects/sessions: " + ", ".join(
        f"{pid}[{','.join(sorted(subjects[pid]))}]" for pid in participant_ids))
    print("=" * 70)


def main():
    output_dir = sys.argv[1] if len(sys.argv) > 1 else str(paths.CONVERSION_DIR)
    bids_dir = sys.argv[2] if len(sys.argv) > 2 else str(paths.BIDS_ROOT)
    if not os.path.isdir(output_dir):
        print(f"ERROR: input folder does not exist: {output_dir}")
        print("  Run dicom_to_bids.py first (it writes the conversion tree under")
        print(f"  {paths.CONVERSION_DIR}).")
        sys.exit(1)
    build_dataset(output_dir, bids_dir)


if __name__ == "__main__":
    main()
