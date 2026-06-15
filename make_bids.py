"""
make_bids.py
============

PURPOSE
-------
Turn the human-readable `BIDS_output/` tree into a SEPARATE, validator-clean
BIDS dataset under `bids/`, so the data is FAIR (Findable, Accessible,
Interoperable, Reusable) and can be fed to BIDS-apps (MRIQC, fMRIPrep/sMRIPrep,
FastSurfer BIDS mode, etc.).

WHY A SEPARATE TREE (not renaming in place)
-------------------------------------------
  * `BIDS_output/` is never modified -> originals stay retrievable and keep
    their descriptive scanner names for human comprehension.
  * `bids/` is regenerated from scratch by this script -> fully reproducible.
  * The scanner protocol name is preserved in the BIDS `acq-` entity, so nothing
    is lost: e.g. `t1_mprage_sag_p2_iso` -> `..._acq-t1mpragesagp2iso_T1w.nii.gz`.

SCOPE (deliberately limited, and documented)
--------------------------------------------
Only the ANATOMICAL scans are curated to BIDS for now (T1w / T2w / FLAIR) - that
is what the structural analysis needs and what BIDS-apps validate cleanly. The
diffusion (incl. derived TRACEW/ADC), fieldmap, ASL/perfusion and SWI series
need their own BIDS curation (ASL in particular requires aslcontext.tsv,
fieldmaps need `IntendedFor`, etc.) and are intentionally left in `BIDS_output/`
until curated, rather than emitting invalid names. This is stated in the
generated README and dataset_description.json.

INPUTS / OUTPUTS
----------------
Reads `BIDS_output/provenance.csv` (the source of truth linking each NIfTI to
its series + category). Writes a BIDS dataset to `bids/`:
    dataset_description.json, README, CHANGES, .bidsignore,
    participants.tsv (+ .json), sub-*/sub-*_sessions.tsv (+ sessions.json),
    sub-*/ses-*/anat/sub-*_ses-*_acq-*_{T1w,T2w,FLAIR}.nii.gz (+ .json)

HOW TO RUN
----------
    python make_bids.py                 # BIDS_output -> bids, both next to script
    python make_bids.py /path/to/BIDS_output /path/to/bids
"""

import os
import re
import csv
import sys
import json
import shutil
from datetime import datetime
from pathlib import Path


BIDS_VERSION = "1.9.0"

# Identifier fields that must never appear in a shared sidecar. deidentify_
# sidecars.py already removes these; we re-check here as defence in depth, since
# `bids/` is the tree most likely to be shared.
IDENTIFIER_FIELDS = [
    "InstitutionName", "InstitutionAddress", "InstitutionalDepartmentName",
    "StationName", "DeviceSerialNumber", "ProcedureStepDescription",
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


def load_anat_rows(output_dir):
    """Read provenance.csv and return only the anatomical rows we can map."""
    prov = os.path.join(output_dir, "provenance.csv")
    if not os.path.isfile(prov):
        print(f"ERROR: {prov} not found. Run dicom_to_bids.py first.")
        sys.exit(1)
    rows = []
    with open(prov, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("category") != "anat":
                continue
            suffix = anat_suffix(row.get("series_description"))
            if suffix is None:
                continue
            row["_suffix"] = suffix
            rows.append(row)
    return rows


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
    rows = load_anat_rows(output_dir)
    if not rows:
        print("No anatomical rows found in provenance.csv; nothing to do.")
        return

    # Fresh, deterministic output: clear any previous bids/ imaging so a re-run
    # never leaves stale files. (We rebuild every file below.)
    if os.path.isdir(bids_dir):
        shutil.rmtree(bids_dir)
    os.makedirs(bids_dir, exist_ok=True)

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

    # ----- dataset-level metadata (the FAIR skeleton) ------------------------
    write_json(os.path.join(bids_dir, "dataset_description.json"), {
        "Name": "CBR / NEUROCOVID structural MRI",
        "BIDSVersion": BIDS_VERSION,
        "DatasetType": "raw",
        "GeneratedBy": [{
            "Name": "CBR pipeline (dicom_to_bids.py + make_bids.py)",
            "Description": "dcm2niix conversion, de-identification, and BIDS "
                           "curation of anatomical scans.",
        }],
        "HowToAcknowledge": "Please contact the CBR / NEUROCOVID study team.",
    })

    write_text(os.path.join(bids_dir, "README"),
        "CBR / NEUROCOVID structural MRI (BIDS)\n"
        "======================================\n\n"
        "BIDS-valid dataset generated by make_bids.py from the project's\n"
        "DICOM->NIfTI pipeline. JSON sidecars are de-identified (DICOM PS3.15\n"
        "Basic Confidentiality Profile). Subject codes are study codes only.\n\n"
        "SCOPE\n"
        "-----\n"
        "Currently curated: ANATOMICAL scans only (T1w / T2w / FLAIR). The\n"
        "scanner protocol name is preserved in the `acq-` entity.\n\n"
        "Sessions follow the study visit scheme:\n"
        "  ses-1 = baseline (0 months)\n"
        "  ses-2 = 6-month follow-up\n"
        "  ses-3 = 12-month follow-up\n"
        "(Subjects present only at later visits correctly start at ses-2/ses-3.)\n\n"
        "NOT YET CURATED: diffusion (incl. derived TRACEW/ADC), fieldmap,\n"
        "ASL/perfusion and SWI series remain in the project's BIDS_output/ tree\n"
        "(treated as sourcedata) pending BIDS curation - ASL needs aslcontext.tsv,\n"
        "fieldmaps need IntendedFor, etc.\n\n"
        "Defaced anatomicals (FreeSurfer mideface) live under\n"
        "BIDS_output/derivatives/defaced/ and can be substituted before sharing.\n")

    write_text(os.path.join(bids_dir, "CHANGES"),
        f"1.0.0 {datetime.now().strftime('%Y-%m-%d')}\n"
        "  - Initial BIDS release: anatomical scans (T1w/T2w/FLAIR), all visits.\n")

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
    print(f" DONE. Wrote {n_copied} anatomical scan(s) for "
          f"{len(participant_ids)} subject(s) to:")
    print(f"   {bids_dir}")
    print(" Subjects/sessions: " + ", ".join(
        f"{pid}[{','.join(sorted(subjects[pid]))}]" for pid in participant_ids))
    print("=" * 70)


def main():
    here = Path(__file__).resolve().parent
    output_dir = sys.argv[1] if len(sys.argv) > 1 else str(here / "BIDS_output")
    bids_dir = sys.argv[2] if len(sys.argv) > 2 else str(here / "bids")
    if not os.path.isdir(output_dir):
        print(f"ERROR: input folder does not exist: {output_dir}")
        sys.exit(1)
    build_dataset(output_dir, bids_dir)


if __name__ == "__main__":
    main()
