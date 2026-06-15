"""
deface_anat.py
==============

PURPOSE
-------
Remove facial features from the ANATOMICAL scans only (T1 / T2 / FLAIR), so the
high-resolution structural images cannot be used to reconstruct a face. This is
the de-identification step that matters most for sharing/archiving 3D structural
MRI, and it is the recommended practice before (or alongside) FreeSurfer.

We use **mideface**, the defacing tool bundled with FreeSurfer 7.3+. It is well
validated and, crucially, adds NO extra dependency beyond FreeSurfer itself
(which this project needs anyway for the structural analysis). It masks face/ear
voxels rather than warping the brain, so it is conservative.

WHAT THIS SCRIPT DOES / DOES NOT DO
-----------------------------------
  * Processes ONLY files inside each   sub-XXX/ses-Y/anat/   folder.
    It never touches dwi / perf / swi / fmap / misc (defacing those gives no
    benefit and can clip brain on low-resolution images).
  * NEVER modifies or deletes the originals. The original anat NIfTI/JSON stay
    exactly where they are. Defaced copies are written to a SEPARATE tree:
        BIDS_output/derivatives/defaced/sub-XXX/ses-Y/anat/
    so the raw anat/ folder remains pristine and fully retrievable.
  * Copies the (already de-identified) JSON sidecar next to each defaced image
    and adds a small provenance note ("DefacedWith").
  * Writes a log:  derivatives/defaced/deface_log.txt
  * Is idempotent: a scan already defaced is skipped (use --force to redo).

HOW TO RUN
----------
    python deface_anat.py                 # default BIDS_output next to this file
    python deface_anat.py /path/to/BIDS_output
    python deface_anat.py --dry-run       # list what WOULD be defaced, run nothing
    python deface_anat.py --force         # re-deface even if output exists

REQUIREMENTS
------------
FreeSurfer 7.3+ installed and sourced, so that 'mideface' is on the PATH
(i.e. FREESURFER_HOME is set and $FREESURFER_HOME/bin is on PATH). This script
runs on whichever machine has FreeSurfer (this Mac, or the dedicated recon Mac).
The --dry-run mode needs nothing installed.
"""

import os
import sys
import json
import shutil
import argparse
import subprocess
from datetime import datetime
from pathlib import Path


# Only these anat suffixes/keywords are defaced. Everything in anat/ is
# structural, but we keep an explicit allow-list as a second safety net so a
# stray non-structural file dropped into anat/ is skipped, not defaced blindly.
ANAT_KEYWORDS = ["t1", "t2", "flair", "mprage", "mp2rage", "space", "tse",
                 "vibe", "spgr", "bravo", "tfl", "t1w", "t2w", "pd"]


def log(message, log_lines):
    """Print a message AND remember it for the log file."""
    print(message)
    log_lines.append(message)


def find_mideface():
    """Locate the 'mideface' executable, or return None if FreeSurfer is absent."""
    found = shutil.which("mideface")
    if found:
        return found
    # Fall back to $FREESURFER_HOME/bin in case the env is set but not on PATH.
    fs_home = os.environ.get("FREESURFER_HOME")
    if fs_home:
        candidate = os.path.join(fs_home, "bin", "mideface")
        if os.path.exists(candidate):
            return candidate
    return None


def looks_anatomical(filename):
    """True if the file name contains a known structural keyword."""
    text = filename.lower()
    return any(k in text for k in ANAT_KEYWORDS)


def find_anat_niftis(output_dir):
    """
    Find every anatomical NIfTI under sub-*/ses-*/anat/, skipping anything that
    is already a defaced output (defensive; defaced files live in a separate
    tree, but we guard against re-processing regardless).
    """
    results = []
    root = Path(output_dir)
    for nifti in sorted(root.glob("sub-*/ses-*/anat/*.nii.gz")):
        if "derivatives" in nifti.parts:
            continue
        if not looks_anatomical(nifti.name):
            continue
        results.append(nifti)
    return results


def deface_one(mideface_path, src_nifti, output_dir, log_lines, force):
    """
    Deface a single anatomical NIfTI into the derivatives/defaced mirror.

    Returns one of: "defaced", "skipped", "failed".
    """
    # Build the mirror path: BIDS_output/derivatives/defaced/<same relative path>.
    rel = os.path.relpath(str(src_nifti), output_dir)
    dest_nifti = os.path.join(output_dir, "derivatives", "defaced", rel)
    os.makedirs(os.path.dirname(dest_nifti), exist_ok=True)

    if os.path.exists(dest_nifti) and not force:
        log(f"    [skip] already defaced: {rel}", log_lines)
        return "skipped"

    # Run mideface. Minimal, robust invocation; mideface picks the output format
    # from the extension and uses FreeSurfer's bundled face template.
    command = [mideface_path, "--i", str(src_nifti), "--o", dest_nifti]
    log(f"    defacing {rel}", log_lines)
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=3600)
    except Exception as err:
        log(f"    [error] mideface failed to launch: {err}", log_lines)
        return "failed"

    if result.returncode != 0 or not os.path.exists(dest_nifti):
        tail = (result.stdout + result.stderr).strip().splitlines()[-5:]
        for line in tail:
            log(f"      mideface> {line}", log_lines)
        log(f"    [error] mideface did not produce output for {rel}", log_lines)
        return "failed"

    # Copy the (already de-identified) JSON sidecar and add a provenance note.
    src_json = src_nifti.with_suffix("").with_suffix(".json")
    if src_json.exists():
        dest_json = Path(dest_nifti).with_suffix("").with_suffix(".json")
        try:
            with open(src_json, "r", encoding="utf-8") as f:
                sidecar = json.load(f)
            sidecar["DefacedWith"] = "FreeSurfer mideface"
            with open(dest_json, "w", encoding="utf-8") as f:
                json.dump(sidecar, f, indent="\t", ensure_ascii=False)
                f.write("\n")
        except Exception as err:
            log(f"    [warn] could not copy sidecar for {rel}: {err}", log_lines)

    return "defaced"


def main():
    parser = argparse.ArgumentParser(
        description="Deface anatomical scans (T1/T2/FLAIR) with FreeSurfer mideface.")
    parser.add_argument("output_dir", nargs="?",
                        default=str(Path(__file__).resolve().parent / "BIDS_output"),
                        help="BIDS_output folder (default: next to this script).")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be defaced; run nothing.")
    parser.add_argument("--force", action="store_true",
                        help="Re-deface even if the output already exists.")
    args = parser.parse_args()

    output_dir = args.output_dir
    if not os.path.isdir(output_dir):
        print(f"ERROR: output folder does not exist: {output_dir}")
        sys.exit(1)

    log_lines = []
    log("=" * 70, log_lines)
    log(" Deface anatomical scans (mideface)", log_lines)
    log(f" started: {datetime.now().isoformat(timespec='seconds')}", log_lines)
    log(f" target : {output_dir}", log_lines)
    log("=" * 70, log_lines)

    anat_niftis = find_anat_niftis(output_dir)
    if not anat_niftis:
        log("No anatomical NIfTI files found under sub-*/ses-*/anat/.", log_lines)
        return

    log(f" found {len(anat_niftis)} anatomical scan(s) to consider.", log_lines)

    # --- dry run: just show the plan, touch nothing, need no FreeSurfer -------
    if args.dry_run:
        log("", log_lines)
        log(" DRY RUN - the following would be defaced into "
            "derivatives/defaced/:", log_lines)
        for nifti in anat_niftis:
            log(f"    {os.path.relpath(str(nifti), output_dir)}", log_lines)
        log("", log_lines)
        log(" (no files written, mideface not called)", log_lines)
        return

    # --- real run: mideface is required --------------------------------------
    mideface_path = find_mideface()
    if not mideface_path:
        log("ERROR: 'mideface' not found. It ships with FreeSurfer 7.3+.",
            log_lines)
        log("  Install/source FreeSurfer (so FREESURFER_HOME is set and",
            log_lines)
        log("  mideface is on PATH), then run this script again. Or run with",
            log_lines)
        log("  --dry-run to preview the plan without FreeSurfer.", log_lines)
        sys.exit(1)
    log(f" using mideface: {mideface_path}", log_lines)
    log("", log_lines)

    counts = {"defaced": 0, "skipped": 0, "failed": 0}
    for nifti in anat_niftis:
        status = deface_one(mideface_path, nifti, output_dir, log_lines,
                            args.force)
        counts[status] += 1

    log("", log_lines)
    log("=" * 70, log_lines)
    log(f" DONE. defaced={counts['defaced']}  skipped={counts['skipped']}  "
        f"failed={counts['failed']}", log_lines)
    log(" Defaced copies (originals untouched) are under:", log_lines)
    log(f"   {os.path.join(output_dir, 'derivatives', 'defaced')}", log_lines)
    log("=" * 70, log_lines)

    # Write the log next to the defaced outputs.
    log_dir = os.path.join(output_dir, "derivatives", "defaced")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "deface_log.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(log_lines))


if __name__ == "__main__":
    main()
