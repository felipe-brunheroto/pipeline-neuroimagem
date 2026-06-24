"""
setup_workspace.py
==================

WHAT THIS IS (in plain language)
--------------------------------
The very first thing a fresh machine needs is a clear, correctly-named place to
DROP the patient DICOM data, so nothing is misnamed or put in the wrong spot from
the start. This small helper does exactly that:

  * ensure_input_folder() -- creates the "Patient Files" folder if it does not
    exist yet, and drops a short README inside explaining what goes there.
  * diagnose_data_folder() -- looks at a folder and says, in plain English,
    whether it is the RIGHT place (a folder that CONTAINS one sub-folder per
    exam) or a common mistake (you pointed INSIDE one exam, or the folder is
    empty / missing), so a bad run is caught before it starts.

It is imported by check_environment.py (to set the workspace up on the first
check) and by dicom_to_bids.py (to guard the conversion). You can also run it
directly:

    python setup_workspace.py

It changes nothing except (optionally) creating the empty input folder + README.
It NEVER touches DICOM data.
"""

import os
from pathlib import Path

# The folder, next to this script, where subject DICOM folders should be dropped.
_HERE = Path(__file__).resolve().parent
DEFAULT_INPUT_NAME = "Patient Files"
PLACEHOLDER_NAME = "READ_ME_DROP_DICOMS_HERE.txt"

PLACEHOLDER_TEXT = """\
DROP YOUR DICOM DATA HERE
=========================

Put ONE FOLDER PER EXAM directly inside this "Patient Files" folder.
Each exam folder holds that scan's DICOM files (any sub-folders are fine).

CORRECT layout:

    Patient Files/
        CBR-003/        <- one subject/visit (its DICOMs inside, at any depth)
        CBR-003.3/      <- same subject, a later visit
        CBR-052/
        STUDY2-07/      <- a different project is fine too; any folder name works

COMMON MISTAKES (the pipeline will warn you about these):

  * Pointing INSIDE one exam (e.g. dropping the contents of CBR-003 here, or a
    bare "DICOM" folder). Put the WHOLE exam folder here instead.
  * Leaving this folder empty. Nothing will be converted.

This file is just a guide. You can delete it once you have added your data.
Remember: patient data stays on THIS computer only (see CLAUDE.md).
"""

# Sub-folder names that are scanner "packaging", NOT exam codes. If the only
# DICOM-bearing sub-folder has one of these names, the user almost certainly
# pointed one level too deep (into a single exam) instead of the folder that
# CONTAINS the exams. These are generic scanner conventions.
GENERIC_SUBFOLDER_NAMES = {
    "dicom", "dicomdir", "images", "image", "imagens", "img", "dcm",
    "scans", "scan", "study", "studies", "exam", "data",
}


def _is_dicom_file(path):
    """Fast DICOM check: real DICOM files carry 'DICM' at byte offset 128."""
    try:
        with open(path, "rb") as f:
            f.seek(128)
            return f.read(4) == b"DICM"
    except Exception:
        return False


def _has_dicom(folder, limit=4000):
    """True if at least ONE DICOM file lives anywhere under `folder`. Stops at the
    first hit (fast for the common case); `limit` avoids crawling huge non-DICOM
    trees forever."""
    checked = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            checked += 1
            if checked > limit:
                return False
            if _is_dicom_file(os.path.join(root, name)):
                return True
    return False


def _exams_in(folder):
    """Immediate sub-folders of `folder` that contain DICOM files (the exams)."""
    found = []
    try:
        names = sorted(os.listdir(folder))
    except Exception:
        return found
    for name in names:
        sub = os.path.join(folder, name)
        if os.path.isdir(sub) and _has_dicom(sub):
            found.append(name)
    return found


def ensure_input_folder(root=None, input_name=DEFAULT_INPUT_NAME):
    """
    Make sure the input folder exists. Create it (and a README placeholder) if it
    does not. Returns (path, created) where `created` is True if we just made it.

    This is safe to call every run: if the folder already exists we leave it and
    its contents completely alone.
    """
    root = Path(root) if root else _HERE
    input_dir = root / input_name
    created = False
    if not input_dir.exists():
        input_dir.mkdir(parents=True, exist_ok=True)
        created = True
    placeholder = input_dir / PLACEHOLDER_NAME
    # Only (re)write the placeholder when the folder has no exams yet, so we never
    # litter a populated data folder.
    if not _exams_in(str(input_dir)) and not placeholder.exists():
        try:
            placeholder.write_text(PLACEHOLDER_TEXT, encoding="utf-8")
        except Exception:
            pass
    return str(input_dir), created


def diagnose_data_folder(path):
    """
    Inspect a candidate data folder and explain, in plain English, what it found.
    Returns one of: 'ok', 'pointed_inside_exam', 'empty', 'missing'.
    """
    path = Path(path)
    print("\n  Checking the data folder:")
    print(f"    {path}")

    if not path.is_dir():
        print("\n  [x] That folder does not exist (or is not a folder).")
        print("      Create it / drop your exam folders in, then try again.")
        return "missing"

    exams = _exams_in(str(path))
    if exams:
        # Classic mistake: a single DICOM sub-folder with a generic scanner name
        # almost always means the user pointed INSIDE one exam.
        if len(exams) == 1 and exams[0].strip().lower() in GENERIC_SUBFOLDER_NAMES:
            print(f"\n  [!] Found DICOMs inside a sub-folder named '{exams[0]}'.")
            print("      That is usually the contents of ONE exam, not the folder")
            print("      that holds several exams. Point one level ABOVE (the")
            print("      folder that contains exam folders, e.g. CBR-003, CBR-052).")
            return "pointed_inside_exam"
        print(f"\n  [ok] Found {len(exams)} exam folder(s) with DICOM data:")
        for name in exams[:8]:
            print(f"        - {name}")
        if len(exams) > 8:
            print(f"        ... and {len(exams) - 8} more.")
        return "ok"

    # No exam sub-folders. Maybe the user pointed straight at ONE exam.
    if _has_dicom(str(path)):
        print("\n  [!] This folder has loose DICOM files but no exam sub-folders.")
        print("      It looks like you pointed at a SINGLE exam. Point at the")
        print("      folder that CONTAINS the exam folders (one level above).")
        return "pointed_inside_exam"

    print("\n  [x] No DICOM files found here.")
    print("      Expected sub-folders (one per exam), each with scanner files.")
    return "empty"


def main():
    print("=" * 70)
    print(" WORKSPACE SETUP  -  prepare the DICOM input folder")
    print("=" * 70)
    input_dir, created = ensure_input_folder()
    if created:
        print(f"\n  Created the input folder:\n    {input_dir}")
        print(f"  A short guide was placed inside ({PLACEHOLDER_NAME}).")
    else:
        print(f"\n  Input folder already exists:\n    {input_dir}")
    state = diagnose_data_folder(input_dir)
    print("\n" + "-" * 70)
    if state == "ok":
        print(" READY: exam folders detected. You can run dicom_to_bids.py next.")
    else:
        print(" NEXT: drop one folder per exam into the input folder above,")
        print("       then re-run this (or check_environment.py).")
    print("-" * 70)


if __name__ == "__main__":
    main()
