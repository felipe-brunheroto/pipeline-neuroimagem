"""
qc_status.py
============

The visual quality-control (QC) record: one row per session and kind of
output, saying whether a person looked at it and what they decided.

  kind    what is checked                      opened with
  recon   FreeSurfer surfaces (cross run)      freeview T1/wm/brainmask/aseg + white/pial
  samseg  SAMSEG lesion mask on the FLAIR      freeview FLAIR_reg + lesion_mask

The table lives in bids/derivatives/qc/qc_status.tsv (paths.QC_FILE), next to
the outputs it judges, with columns:
  subject, session, kind, status (pass | fail), note, reviewer, reviewed (date
  of the review, not of the exam)
Recording the same session and kind again replaces the earlier row.

run_batch.py lists what is finished but not reviewed yet ("QC to do"), and the
student menu (Pipeline_Alunos/passos.py) walks through it. Standard library
only, and every function takes the folders as arguments, so the menu can use it
without importing paths.py.
"""

import csv
import json
import os
import re
from datetime import date

FIELDS = ["subject", "session", "kind", "status", "note", "reviewer", "reviewed"]
KINDS = ("recon", "samseg")
STATUSES = ("pass", "fail")

# A cross-sectional FreeSurfer id ('sub-X_ses-Y'): base and long runs are
# reviewed through their timepoints.
CROSS_ID = re.compile(r"^(sub-[A-Za-z0-9]+)_(ses-[A-Za-z0-9]+)$")


def load(qc_path):
    """{(subject, session, kind): row} of every decision recorded so far."""
    if not os.path.isfile(qc_path):
        return {}
    with open(qc_path, newline="", encoding="utf-8") as f:
        return {(r["subject"], r["session"], r["kind"]): r
                for r in csv.DictReader(f, delimiter="\t")}


def clean_note(text):
    """One line, no tabs, at most 300 characters (it goes into a TSV)."""
    return " ".join((text or "").split())[:300]


def record(qc_path, subject, session, kind, status, note="", reviewer=""):
    """Save one decision (replacing an earlier one for the same session/kind)."""
    if kind not in KINDS or status not in STATUSES:
        raise ValueError(f"kind must be one of {KINDS}, status one of {STATUSES}")
    rows = load(qc_path)
    rows[(subject, session, kind)] = {
        "subject": subject, "session": session, "kind": kind, "status": status,
        "note": clean_note(note), "reviewer": clean_note(reviewer)[:20],
        "reviewed": date.today().isoformat()}
    folder = os.path.dirname(qc_path)
    os.makedirs(folder, exist_ok=True)
    description = os.path.join(folder, "dataset_description.json")
    if not os.path.isfile(description):
        with open(description, "w", encoding="utf-8") as f:
            json.dump({"Name": "Visual quality control decisions",
                       "BIDSVersion": "1.9.0", "DatasetType": "derivative",
                       "GeneratedBy": [{"Name": "Pipeline Neuroimagem QC "
                                                "(student menu)"}]},
                      f, indent="\t")
            f.write("\n")
    tmp = qc_path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows[k] for k in sorted(rows))
    os.replace(tmp, qc_path)


def finished(freesurfer_dir, samseg_dir):
    """Every output ready to be reviewed: [{subject, session, kind, folder}]."""
    items = []
    if os.path.isdir(freesurfer_dir):
        for name in sorted(os.listdir(freesurfer_dir)):
            m = CROSS_ID.match(name)
            folder = os.path.join(freesurfer_dir, name)
            if m and os.path.isfile(os.path.join(folder, "scripts", "recon-all.done")):
                items.append({"subject": m.group(1), "session": m.group(2),
                              "kind": "recon", "folder": folder})
    if os.path.isdir(samseg_dir):
        for name in sorted(os.listdir(samseg_dir)):
            m = CROSS_ID.match(name)
            folder = os.path.join(samseg_dir, name)
            if (m and os.path.isfile(os.path.join(folder, "samseg.stats"))
                    and os.path.isfile(os.path.join(folder, "lesion_mask.mgz"))):
                items.append({"subject": m.group(1), "session": m.group(2),
                              "kind": "samseg", "folder": folder})
    return items


def pending(freesurfer_dir, samseg_dir, qc_path):
    """Finished outputs without a decision yet."""
    done = load(qc_path)
    return [i for i in finished(freesurfer_dir, samseg_dir)
            if (i["subject"], i["session"], i["kind"]) not in done]


def failed(qc_path):
    """Rows marked 'fail' (they wait for a decision on what to redo)."""
    return [r for r in load(qc_path).values() if r["status"] == "fail"]


def freeview_command(item):
    """(folder, args): the freeview command to review one item, with paths
    relative to its session folder (run it from there)."""
    if item["kind"] == "recon":
        return item["folder"], [
            "freeview", "-v", "mri/T1.mgz", "mri/wm.mgz:colormap=heat:opacity=0.4",
            "mri/brainmask.mgz", "mri/aseg.mgz:colormap=lut:opacity=0.2",
            "-f", "surf/lh.white:edgecolor=blue", "surf/lh.pial:edgecolor=red",
            "surf/rh.white:edgecolor=blue", "surf/rh.pial:edgecolor=red"]
    return item["folder"], ["freeview", "-v", "FLAIR_reg.nii.gz",
                            "lesion_mask.mgz:colormap=heat:opacity=0.4"]


def surface_holes(fs_subject_dir):
    """Topological defects before the fix (from stats/aseg.stats), or None. The
    Euler number is 2 - 2 x holes; many more holes than other sessions usually
    means a poor scan or a bad surface."""
    try:
        with open(os.path.join(fs_subject_dir, "stats", "aseg.stats"),
                  encoding="utf-8") as f:
            for line in f:
                if line.startswith("# Measure SurfaceHoles"):
                    return int(float(line.split(",")[3]))
    except (OSError, ValueError, IndexError):
        pass
    return None


def lesion_volume(samseg_session_dir):
    """Lesion volume in mm3 from samseg.stats, or None."""
    try:
        with open(os.path.join(samseg_session_dir, "samseg.stats"),
                  encoding="utf-8") as f:
            for line in f:
                if line.startswith("# Measure Lesions"):
                    return float(line.split(",")[1])
    except (OSError, ValueError, IndexError):
        pass
    return None
