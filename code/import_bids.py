"""
import_bids.py
==============

PURPOSE
-------
For a project whose images arrive ALREADY CONVERTED to BIDS by another team
(``"input": "bids"`` in projects/<NAME>/project.json), this is step 1: it takes
the delivered subject folders and builds this project's de-identified BIDS
dataset, ready for defacing and FreeSurfer (steps 5-7).

The delivered copy in ``projects/<NAME>/sourcedata/received/`` is READ-ONLY: it
is never modified, so the import can always be redone from it.

WHAT IT DOES, per delivered sub-* folder
----------------------------------------
  1. Session level: if the subject has no ses-* folder, its files are filed
     under the project's ``default_session`` (e.g. ses-1) and the label is
     added to every file name (sub-X_acq-3d_T1w -> sub-X_ses-1_acq-3d_T1w).
  2. De-identification: every JSON sidecar loses the identifier fields listed
     in make_bids.IDENTIFIER_FIELDS (institution, station, device serial,
     StudyDescription, patient fields...). Values are never logged.
  3. Scanner-derived images (ImageType contains DERIVED, e.g. a TRACEW run
     filed under dwi/) are NOT raw data: they go to
     bids/derivatives/scanner-derived/ with a desc-<type> entity, the same
     place make_derivatives.py uses for DICOM-input projects. Their gradient
     files are dropped (a trace image has no gradient table).
  4. Dataset files: dataset_description.json, README, CHANGES, .bidsignore and
     participants.tsv/.json are written if missing; participants.tsv gains any
     new subject.
  5. Checks: every image has a sidecar, BOLD has TaskName + RepetitionTime,
     DWI has .bval/.bvec, and no identifier field is left.

Existing files in bids/ are never overwritten (re-running is safe); use
--force to rebuild a subject that was already imported.

HOW TO RUN
----------
    python code/import_bids.py --project COGAGING
    python code/import_bids.py --project COGAGING "/path/to/delivered/folder"
    python code/import_bids.py --project COGAGING --dry-run
"""

import argparse
import csv
import json
import os
import re
import shutil
import sys
from datetime import datetime

import make_bids as mb
import paths

IMAGE_EXT = (".nii.gz", ".nii")
SIDE_EXT = (".json", ".bval", ".bvec", ".tsv")


def split_ext(name):
    for ext in IMAGE_EXT + SIDE_EXT:
        if name.endswith(ext):
            return name[:-len(ext)], ext
    return os.path.splitext(name)


def add_session(stem, sub, ses):
    """'sub-X_acq-3d_T1w' -> 'sub-X_ses-1_acq-3d_T1w' (unchanged if ses- present)."""
    if "_ses-" in stem:
        return stem
    return stem.replace(sub, f"{sub}_{ses}", 1)


def add_desc(stem, desc):
    """Insert desc-<label> just before the suffix: ..._run-02_dwi -> ..._run-02_desc-TRACEW_dwi."""
    head, _, suffix = stem.rpartition("_")
    return f"{head}_desc-{desc}_{suffix}"


def derived_label(sidecar):
    """True/label if the sidecar says the image is scanner-derived (ImageType DERIVED)."""
    image_type = [str(t).upper() for t in sidecar.get("ImageType", [])]
    if not image_type or image_type[0] != "DERIVED":
        return None
    # The scanner names the map in ImageType (e.g. TRACEW, ADC, FA); fall back to 'derived'.
    for token in ("TRACEW", "ADC", "FA", "EXP", "SWI", "MNIP", "PERFUSION"):
        if token in image_type:
            return token
    return "derived"


def scrub(sidecar):
    """Remove identifier fields in place; return the names removed."""
    removed = [f for f in mb.IDENTIFIER_FIELDS if f in sidecar]
    for f in removed:
        del sidecar[f]
    return removed


def plan_subject(sub_dir, default_session):
    """
    Return a list of (src, dest_rel, kind) for one delivered subject.
    kind: 'raw' | 'derived' | 'drop'. dest_rel is relative to the BIDS root.
    """
    sub = os.path.basename(sub_dir)
    has_sessions = any(n.startswith("ses-") for n in os.listdir(sub_dir))
    sessions = ([n for n in sorted(os.listdir(sub_dir)) if n.startswith("ses-")]
                if has_sessions else [None])
    plan = []
    for ses_name in sessions:
        ses = ses_name or default_session
        ses_dir = os.path.join(sub_dir, ses_name) if ses_name else sub_dir
        for datatype in sorted(os.listdir(ses_dir)):
            dt_dir = os.path.join(ses_dir, datatype)
            if not os.path.isdir(dt_dir) or datatype.startswith("."):
                continue
            # Group files by stem so an image and its sidecars travel together.
            groups = {}
            for name in sorted(os.listdir(dt_dir)):
                if name.startswith("."):
                    continue
                stem, ext = split_ext(name)
                groups.setdefault(stem, []).append((name, ext))
            for stem, files in groups.items():
                sidecar = {}
                json_name = next((n for n, e in files if e == ".json"), None)
                if json_name:
                    with open(os.path.join(dt_dir, json_name), encoding="utf-8") as f:
                        sidecar = json.load(f)
                desc = derived_label(sidecar)
                new_stem = add_session(stem, sub, ses)
                for name, ext in files:
                    src = os.path.join(dt_dir, name)
                    if desc:
                        if ext in (".bval", ".bvec"):
                            plan.append((src, None, "drop"))
                            continue
                        rel = os.path.join("derivatives", "scanner-derived", sub, ses,
                                           datatype, add_desc(new_stem, desc) + ext)
                        plan.append((src, rel, "derived"))
                    else:
                        rel = os.path.join(sub, ses, datatype, new_stem + ext)
                        plan.append((src, rel, "raw"))
    return plan


def write_if_missing(path, writer, content):
    if not os.path.exists(path):
        writer(path, content)
        return True
    return False


def write_dataset_files(bids_dir, subjects):
    title = paths.PROJECT["title"]
    write_if_missing(os.path.join(bids_dir, "dataset_description.json"), mb.write_json, {
        "Name": f"{title} MRI",
        "BIDSVersion": mb.BIDS_VERSION,
        "DatasetType": "raw",
        "GeneratedBy": [{
            "Name": "Pipeline Neuroimagem (import_bids.py)",
            "Description": "Imported from BIDS delivered by the acquisition team: "
                           "session level added, sidecars de-identified, "
                           "scanner-derived images moved to derivatives/.",
        }],
        "HowToAcknowledge": f"Please contact the {title} study team.",
    })
    write_if_missing(os.path.join(bids_dir, "README"), mb.write_text,
        f"{title} MRI (BIDS)\n"
        + "=" * (len(title) + 11) + "\n\n"
        "Built by code/import_bids.py from BIDS delivered by the acquisition team\n"
        "(kept unchanged in projects/<NAME>/sourcedata/received/, outside this\n"
        "dataset). JSON sidecars are de-identified (DICOM PS3.15 Basic\n"
        "Confidentiality Profile + StudyDescription). Subject codes are study\n"
        "codes only.\n\n"
        "Scanner-derived images delivered among the raw data (e.g. TRACEW) are\n"
        "under derivatives/scanner-derived/. Defaced anatomicals (FreeSurfer\n"
        "mideface) live under derivatives/defaced/.\n")
    write_if_missing(os.path.join(bids_dir, "CHANGES"), mb.write_text,
        f"1.0.0 {datetime.now().strftime('%Y-%m-%d')}\n"
        "  - Initial import of delivered BIDS.\n")
    write_if_missing(os.path.join(bids_dir, ".bidsignore"), mb.write_text, "")

    deriv = os.path.join(bids_dir, "derivatives", "scanner-derived")
    if os.path.isdir(deriv):
        write_if_missing(os.path.join(deriv, "dataset_description.json"), mb.write_json, {
            "Name": f"{title} scanner-derived maps",
            "BIDSVersion": mb.BIDS_VERSION,
            "DatasetType": "derivative",
            "GeneratedBy": [{
                "Name": "import_bids.py",
                "Description": "Scanner-produced derived images found among the "
                               "delivered raw data, renamed with desc- entities. "
                               "These are NOT raw acquisitions.",
            }],
        })

    # participants.tsv: keep existing rows (and any columns filled in by the
    # team), append new subjects.
    tsv = os.path.join(bids_dir, "participants.tsv")
    header, rows = ["participant_id"], []
    if os.path.isfile(tsv):
        with open(tsv, newline="", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t")
            header = next(reader, header)
            rows = list(reader)
    known = {r[0] for r in rows if r}
    for sub in sorted(subjects):
        if sub not in known:
            rows.append([sub] + ["n/a"] * (len(header) - 1))
    with open(tsv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(header)
        w.writerows(sorted(rows))
    write_if_missing(os.path.join(bids_dir, "participants.json"), mb.write_json, {
        "participant_id": {"Description": "Study subject code (no personal identifiers)."},
    })


def check_dataset(bids_dir, subjects):
    """Light structural checks on the imported subjects; returns a list of problems."""
    problems = []
    for sub in subjects:
        for root, _dirs, files in os.walk(os.path.join(bids_dir, sub)):
            for name in files:
                stem, ext = split_ext(name)
                path = os.path.join(root, name)
                if ext in IMAGE_EXT:
                    if not os.path.isfile(os.path.join(root, stem + ".json")):
                        problems.append(f"no sidecar for {name}")
                    if stem.endswith("_dwi"):
                        for g in (".bval", ".bvec"):
                            if not os.path.isfile(os.path.join(root, stem + g)):
                                problems.append(f"missing {g} for {name}")
                if ext == ".json":
                    with open(path, encoding="utf-8") as f:
                        sc = json.load(f)
                    left = [k for k in mb.IDENTIFIER_FIELDS if k in sc]
                    if left:
                        problems.append(f"identifier field(s) {left} still in {name}")
                    if stem.endswith("_bold"):
                        for k in ("TaskName", "RepetitionTime"):
                            if k not in sc:
                                problems.append(f"{k} missing in {name}")
    return problems


def main():
    parser = argparse.ArgumentParser(
        description="Import BIDS delivered by another team into this project's BIDS root.")
    parser.add_argument("source", nargs="?", default=str(paths.RECEIVED_DIR),
                        help="Folder holding the delivered sub-* folders "
                             "(default: projects/<NAME>/sourcedata/received).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only print what would be done.")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild subjects that already exist in bids/.")
    args = parser.parse_args()

    if paths.PROJECT.get("input") != "bids":
        sys.exit(f"ERROR: project {paths.PROJECT_NAME} takes DICOM input; "
                 "use dicom_to_bids.py (step 1) instead.")
    source = args.source
    if not os.path.isdir(source):
        sys.exit(f"ERROR: source folder does not exist: {source}")
    default_session = paths.PROJECT.get("default_session", "ses-1")
    prefix = paths.PROJECT.get("subject_prefix", "")
    bids_dir = str(paths.BIDS_ROOT)

    subjects = sorted(n for n in os.listdir(source)
                      if n.startswith("sub-") and os.path.isdir(os.path.join(source, n)))
    if not subjects:
        sys.exit(f"ERROR: no sub-* folders in {source}")
    unexpected = [s for s in subjects if prefix and not re.match(rf"sub-{prefix}\d+$", s)]
    if unexpected:
        sys.exit(f"ERROR: subject code(s) not matching sub-{prefix}<digits>: "
                 + ", ".join(unexpected) + " -- check you picked the right project.")

    log = [f"BIDS import into {bids_dir}",
           f"run: {datetime.now().isoformat(timespec='seconds')}",
           f"source: {source}",
           f"identifier fields removed (values not recorded): "
           + ", ".join(mb.IDENTIFIER_FIELDS),
           "-" * 60]
    imported, skipped = [], []
    counts = {"raw": 0, "derived": 0, "drop": 0}
    for sub in subjects:
        if os.path.isdir(os.path.join(bids_dir, sub)) and not args.force:
            skipped.append(sub)
            log.append(f"{sub}: already in bids/ (use --force to rebuild) - skipped")
            continue
        plan = plan_subject(os.path.join(source, sub), default_session)
        for src, rel, kind in plan:
            counts[kind] += 1
            if kind == "drop":
                log.append(f"{sub}: dropped {os.path.basename(src)} "
                           "(gradient file of a scanner-derived image)")
                continue
            log.append(f"{sub}: {os.path.relpath(src, source)} -> {rel} [{kind}]")
            if args.dry_run:
                continue
            dest = os.path.join(bids_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if src.endswith(".json"):
                with open(src, encoding="utf-8") as f:
                    sidecar = json.load(f)
                removed = scrub(sidecar)
                mb.write_json(dest, sidecar)
                if removed:
                    log.append(f"    de-identified: removed {', '.join(removed)}")
            else:
                shutil.copy2(src, dest)
                # The received copy is kept read-only; the working copy is not.
                os.chmod(dest, 0o644)
        imported.append(sub)

    print("\n".join(log))
    if args.dry_run:
        print(f"\n(dry run) would import {len(imported)} subject(s): "
              f"{counts['raw']} raw file(s), {counts['derived']} derived, "
              f"{counts['drop']} dropped.")
        return

    all_subjects = sorted(n for n in os.listdir(bids_dir) if n.startswith("sub-"))
    write_dataset_files(bids_dir, all_subjects)
    problems = check_dataset(bids_dir, imported)

    log_dir = os.path.join(bids_dir, "sourcedata", "import")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir,
                            f"import_log_{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt")
    log += ["-" * 60,
            f"imported: {', '.join(imported) or 'none'}",
            f"skipped (already present): {', '.join(skipped) or 'none'}",
            f"files: {counts['raw']} raw, {counts['derived']} derived, "
            f"{counts['drop']} dropped",
            "checks: " + ("OK" if not problems else f"{len(problems)} problem(s)")]
    log += [f"  - {p}" for p in problems]
    mb.write_text(log_path, "\n".join(log) + "\n")

    print("\n" + "=" * 70)
    print(f" DONE. Imported {len(imported)} subject(s) into {bids_dir}")
    print(f"   {counts['raw']} raw file(s), {counts['derived']} scanner-derived, "
          f"{counts['drop']} dropped")
    if skipped:
        print(f"   skipped (already present): {', '.join(skipped)}")
    print(" Checks: " + ("OK" if not problems else f"{len(problems)} problem(s):"))
    for p in problems:
        print(f"   - {p}")
    print(f" Log: {log_path}")
    print("=" * 70)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
