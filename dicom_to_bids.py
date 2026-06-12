"""
dicom_to_bids.py
================

WHAT THIS SCRIPT DOES (in plain language)
-----------------------------------------
It takes a folder full of raw DICOM files (the format MRI scanners produce) and:

  1. Looks inside each subject folder and finds the real DICOM files
     (ignoring junk like autorun.exe, .dll files, DICOMDIR, etc.).
  2. Groups those DICOM files by "series" (one series = one scan/sequence),
     using the official unique ID each series carries (SeriesInstanceUID).
  3. Converts each subject's DICOMs to NIfTI (.nii.gz) using the external,
     trusted tool 'dcm2niix'. We do NOT reinvent conversion.
  4. Sorts the resulting files into a SIMPLE, BIDS-like folder layout:
        anat / dwi / fmap / misc
     based on easy keyword rules (not hardcoded exact sequence names).
  5. Writes a PROVENANCE file (provenance.csv) so every NIfTI can be traced
     back to the exact DICOM series it came from.
  6. Writes a VALIDATION report (validation_report.csv) that compares a few
     key values between the original DICOM and the produced JSON, and flags
     any mismatches.

It NEVER modifies or deletes your original DICOM files. It only reads them
and writes new files into a separate output folder.

WHAT YOU NEED INSTALLED (run check_environment.py first)
--------------------------------------------------------
  - Python 3.8+
  - dcm2niix (on your PATH, or installed via 'pip install dcm2niix')
  - pydicom  ('pip install pydicom')  -> used to read DICOM tags

HOW TO RUN IT
-------------
Option A - edit the two paths below (INPUT_DIR / OUTPUT_DIR), then run:

    python dicom_to_bids.py

Option B - pass the paths on the command line (overrides the defaults):

    python dicom_to_bids.py  "C:\\path\\to\\dicoms"  "C:\\path\\to\\output"

When it finishes, look in your OUTPUT_DIR. You will find one folder per
subject plus provenance.csv, validation_report.csv and conversion_log.txt.
"""

# ----------------------------------------------------------------------------
# Standard-library imports (always available, no install needed).
# ----------------------------------------------------------------------------
import os
import sys
import csv
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

# pydicom is the one external library we rely on, to read DICOM tags.
# We import it inside a try/except so that, if it is missing, we give the
# user a clear instruction instead of a confusing crash.
try:
    import pydicom
except ImportError:
    print("ERROR: the 'pydicom' library is not installed.")
    print("Fix it by running:   pip install pydicom")
    sys.exit(1)


# ============================================================================
# 1. CONFIGURATION  -  edit these two paths for your computer if you like.
# ============================================================================
# INPUT_DIR  : the folder that CONTAINS your subject folders (e.g. CBR-003).
# OUTPUT_DIR : where the converted, organized results will be written.
#              It will be created if it does not exist. It is kept SEPARATE
#              from the input so your originals are never touched.
#
# The defaults are computed RELATIVE to this script's own folder, so the
# pipeline runs as-is on any machine (this Mac, a Windows box, etc.) without
# editing paths. You can still override both on the command line (see below).
_HERE = Path(__file__).resolve().parent
INPUT_DIR  = str(_HERE / "Patient Files")   # subject folders live in here
OUTPUT_DIR = str(_HERE / "BIDS_output")     # results are written here


# ============================================================================
# 2. KEYWORD RULES for sorting series into anat / dwi / fmap / misc.
# ============================================================================
# These are matched as lowercase SUBSTRINGS against the series description,
# so we are NOT hardcoding exact sequence names. Add or remove words freely.
# Order of checks (in categorize_series) is:
#     fmap -> perf -> swi -> dwi -> anat -> else misc
# The more specific modalities (fmap/perf/swi) are checked BEFORE the broad
# anat keywords so, e.g., an SWI magnitude image is not mistaken for anatomy.
FMAP_KEYWORDS = ["field_map", "fieldmap", "field map", "fmap", "gre_field",
                 "b0map", "b0 map", "phasediff", "phase diff"]
# Arterial spin labeling and scanner-derived perfusion maps. BIDS gives ASL its
# own official datatype ("perf"), so we route these out of misc into perf/.
PERF_KEYWORDS = ["asl", "perfusion", "perfusion_weighted", "pwi", "pasl",
                 "pcasl", "casl", "_cbf", "cerebral_blood"]
# Susceptibility-weighted imaging family. A Siemens SWI run exports four series:
# magnitude, phase, the combined SWI image and the minIP. BIDS has no canonical
# "swi" datatype, but grouping them in swi/ keeps misc/ clean and is unambiguous.
SWI_KEYWORDS  = ["swi", "susceptibility", "mip_images", "minip", "min_ip",
                 "venobold", "veno_bold", "mag_images", "pha_images"]
DWI_KEYWORDS  = ["dwi", "dti", "dki", "diffusion", "difusao", "ep2d_diff",
                 "_diff", "trace", "_adc", "adc"]
ANAT_KEYWORDS = ["t1", "t2", "flair", "mprage", "mp2rage", "tfl", "spgr",
                 "fspgr", "bravo", "tse", "space", "black blood", "vibe",
                 "mp-rage", "t1w", "t2w", "pd"]


# ============================================================================
# 3. HELPER FUNCTIONS
# ============================================================================

def log(message, log_lines):
    """Print a message to the screen AND remember it for the log file."""
    print(message)
    log_lines.append(message)


def find_dcm2niix():
    """
    Locate the dcm2niix program.

    We try two ways so the script works in as many setups as possible:
      1. A normal install where 'dcm2niix' is on the system PATH.
      2. The pip package 'dcm2niix', which bundles the binary.
    Returns the path to the executable, or None if it cannot be found.
    """
    # Way 1: a plain command on PATH (the usual case after a manual install).
    found = shutil.which("dcm2niix")
    if found:
        return found
    # Way 2: the pip wrapper package, which exposes the bundled binary path.
    try:
        import dcm2niix
        if os.path.exists(dcm2niix.bin):
            return dcm2niix.bin
    except Exception:
        pass
    return None


def is_dicom_file(path):
    """
    Quickly decide whether a file is a DICOM file WITHOUT fully parsing it.

    Standard DICOM files have the 4 letters 'DICM' at byte offset 128.
    Checking these bytes is fast and lets us skip the thousands of non-DICOM
    files (executables, DLLs, text files) found on scanner export CDs.
    """
    try:
        with open(path, "rb") as f:
            f.seek(128)            # jump to the magic-number location
            return f.read(4) == b"DICM"
    except Exception:
        # Unreadable / too small / permission issue -> treat as "not DICOM".
        return False


def safe_label(name):
    """
    Turn a folder name into a safe subject label.
    Example: 'CBR-003' -> 'sub-CBR003'  (we drop characters that aren't letters
    or numbers, to keep file names clean and portable).
    """
    cleaned = "".join(ch for ch in name if ch.isalnum())
    return "sub-" + cleaned


def categorize_series(description):
    """
    Decide which BIDS-like folder a series belongs in, using simple keyword
    matching on the (lowercased) series description.

    Returns one of: 'anat', 'dwi', 'fmap', 'perf', 'swi', 'misc'.
    We check the specific modalities (fmap/perf/swi/dwi) BEFORE anat, because a
    perfusion/diffusion/fieldmap/SWI description should not be misread as
    anatomical.
    """
    text = (description or "").lower()
    if any(k in text for k in FMAP_KEYWORDS):
        return "fmap"
    if any(k in text for k in PERF_KEYWORDS):
        return "perf"
    if any(k in text for k in SWI_KEYWORDS):
        return "swi"
    if any(k in text for k in DWI_KEYWORDS):
        return "dwi"
    if any(k in text for k in ANAT_KEYWORDS):
        return "anat"
    return "misc"


def scan_subject_series(subject_dir, log_lines):
    """
    Walk through one subject's folder, find all DICOM files, and group them by
    SeriesInstanceUID.

    Returns a dictionary:
        { series_uid : {
              "number": SeriesNumber,
              "description": SeriesDescription,
              "modality": Modality,
              "sample_dataset": <one pydicom dataset, used later for validation>,
              "file_count": how many DICOM files were in this series
          }, ... }

    We only read the few tags we need (specific_tags) so this stays fast even
    with thousands of files.
    """
    # Tags we actually need: identity of the series + a couple for validation.
    wanted_tags = ["SeriesInstanceUID", "SeriesNumber", "SeriesDescription",
                   "Modality", "RepetitionTime", "EchoTime", "ProtocolName"]

    series = {}
    n_dicom = 0

    for root, _dirs, files in os.walk(subject_dir):
        for filename in files:
            path = os.path.join(root, filename)

            # Fast skip of obvious non-DICOM files.
            if not is_dicom_file(path):
                continue

            # Read just the tags we care about. force=True helps with files
            # that lack a proper header; try/except keeps one bad file from
            # crashing the whole run.
            try:
                ds = pydicom.dcmread(path, stop_before_pixels=True,
                                     specific_tags=wanted_tags, force=True)
            except Exception as err:
                log(f"    [skip] could not read DICOM '{path}': {err}", log_lines)
                continue

            uid = getattr(ds, "SeriesInstanceUID", None)
            if not uid:
                # Without a series UID we cannot group reliably -> log and skip.
                log(f"    [skip] no SeriesInstanceUID in '{path}'", log_lines)
                continue

            n_dicom += 1
            if uid not in series:
                series[uid] = {
                    "number": getattr(ds, "SeriesNumber", ""),
                    "description": getattr(ds, "SeriesDescription", "") or "",
                    "modality": getattr(ds, "Modality", "") or "",
                    "sample_dataset": ds,   # keep one example for validation
                    "file_count": 0,
                }
            series[uid]["file_count"] += 1

    log(f"    found {n_dicom} DICOM files in {len(series)} series", log_lines)
    return series


def run_dcm2niix(dcm2niix_path, subject_dir, work_dir, subject_label, log_lines):
    """
    Run dcm2niix once for a subject, converting every series it finds.

    Flags used:
      -z y        : compress output to .nii.gz (smaller files)
      -b y        : also write a BIDS-style .json sidecar for each image
      -ba y        : anonymize the BIDS sidecar (no patient identifiers in JSON)
      -o <dir>    : output directory (a temporary 'work' folder)
      -f <pattern>: file-name pattern. %s = series number, %d = description.
      -d 9        : search subfolders up to 9 levels deep (scanner CDs nest
                    files several folders down).

    dcm2niix automatically separates the output by series, so one call handles
    all of a subject's scans. We capture its text output for the log.
    """
    file_pattern = subject_label + "_series%s_%d"
    command = [
        dcm2niix_path,
        "-z", "y",
        "-b", "y",
        "-ba", "y",
        "-o", str(work_dir),
        "-f", file_pattern,
        "-d", "9",
        str(subject_dir),
    ]
    log(f"    running: {' '.join(command)}", log_lines)
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=3600)  # 1 hour safety limit
        # dcm2niix prints lots of progress text; keep the last few lines.
        output = (result.stdout + result.stderr).strip().splitlines()
        for line in output[-8:]:
            log(f"      dcm2niix> {line}", log_lines)
        return result.returncode == 0
    except Exception as err:
        log(f"    [error] dcm2niix failed: {err}", log_lines)
        return False


def match_series_for_json(json_path, series_by_number):
    """
    Given a JSON sidecar that dcm2niix produced, figure out which original
    series it came from.

    dcm2niix's JSON does NOT include SeriesInstanceUID (on purpose, for
    privacy), but it DOES include SeriesNumber. We use SeriesNumber to look
    up the matching series we found during scanning. Returns the series-info
    dict, or None if we cannot match it.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            sidecar = json.load(f)
    except Exception:
        return None, None
    number = sidecar.get("SeriesNumber")
    # SeriesNumber may be int or string; normalize to string for lookup.
    info = series_by_number.get(str(number))
    return info, sidecar


def compare_values(dicom_value, json_value, scale=1.0):
    """
    Compare one DICOM value against one JSON value, returning a status string.

    'scale' converts DICOM units to JSON units where needed. For example,
    DICOM stores RepetitionTime/EchoTime in MILLISECONDS, while the BIDS JSON
    stores them in SECONDS, so we pass scale=0.001 for those.

    Returns: 'OK', 'MISMATCH', or 'MISSING'.
    """
    if dicom_value in (None, "") or json_value in (None, ""):
        return "MISSING"
    try:
        a = float(dicom_value) * scale
        b = float(json_value)
        # Allow a tiny rounding tolerance.
        return "OK" if abs(a - b) < 0.01 else "MISMATCH"
    except (ValueError, TypeError):
        # Fall back to plain text comparison for non-numeric fields.
        return "OK" if str(dicom_value).strip() == str(json_value).strip() else "MISMATCH"


# ============================================================================
# 4. MAIN PROGRAM
# ============================================================================

def main():
    # Allow optional command-line override of the two paths.
    input_dir = sys.argv[1] if len(sys.argv) > 1 else INPUT_DIR
    output_dir = sys.argv[2] if len(sys.argv) > 2 else OUTPUT_DIR

    log_lines = []   # collected and written to conversion_log.txt at the end
    log("=" * 70, log_lines)
    log(f" DICOM -> NIfTI / BIDS-like conversion", log_lines)
    log(f" started: {datetime.now().isoformat(timespec='seconds')}", log_lines)
    log(f" input : {input_dir}", log_lines)
    log(f" output: {output_dir}", log_lines)
    log("=" * 70, log_lines)

    # --- check inputs and tools before doing anything ----------------------
    if not os.path.isdir(input_dir):
        log(f"ERROR: input folder does not exist: {input_dir}", log_lines)
        sys.exit(1)

    dcm2niix_path = find_dcm2niix()
    if not dcm2niix_path:
        log("ERROR: dcm2niix not found. Install it, then run again.", log_lines)
        log("  See check_environment.py for instructions.", log_lines)
        sys.exit(1)
    log(f" using dcm2niix: {dcm2niix_path}", log_lines)

    # Prepare output folder and a temporary 'work' area for raw dcm2niix output.
    os.makedirs(output_dir, exist_ok=True)
    work_root = os.path.join(output_dir, ".work")
    os.makedirs(work_root, exist_ok=True)

    # These lists collect rows for the two CSV reports.
    provenance_rows = []   # subject, series_uid, series_number, description, modality, category, nifti, json
    validation_rows = []   # subject, series_uid, field, dicom_value, json_value, status

    # --- find subject folders ---------------------------------------------
    # A "subject" is any immediate sub-folder of INPUT_DIR that actually
    # contains DICOM files. This automatically skips the output folder and
    # any stray non-imaging folders.
    subjects = []
    for entry in sorted(os.listdir(input_dir)):
        full = os.path.join(input_dir, entry)
        if not os.path.isdir(full):
            continue
        if os.path.abspath(full) == os.path.abspath(output_dir):
            continue   # never treat our own output folder as a subject
        subjects.append((entry, full))

    if not subjects:
        log("No subject sub-folders found. Nothing to do.", log_lines)
        return

    # --- process each subject ---------------------------------------------
    for subject_name, subject_dir in subjects:
        subject_label = safe_label(subject_name)
        log("", log_lines)
        log(f"SUBJECT: {subject_name}  ->  {subject_label}", log_lines)

        # 1) Scan and group this subject's DICOMs by series.
        series = scan_subject_series(subject_dir, log_lines)
        if not series:
            log("    (no DICOM series found here; skipping)", log_lines)
            continue

        # Build a quick lookup from SeriesNumber -> series info, so we can match
        # dcm2niix's JSON output (which carries SeriesNumber) back to the UID.
        series_by_number = {}
        for uid, info in series.items():
            series_by_number[str(info["number"])] = dict(info, uid=uid)

        # 2) Convert with dcm2niix into a per-subject temporary work folder.
        work_dir = os.path.join(work_root, subject_label)
        os.makedirs(work_dir, exist_ok=True)
        ok = run_dcm2niix(dcm2niix_path, subject_dir, work_dir,
                          subject_label, log_lines)
        if not ok:
            log("    [warn] dcm2niix reported a problem; check the log.", log_lines)

        # 3) For each produced NIfTI, sort it into anat/dwi/fmap/misc, record
        #    provenance, and validate against the original DICOM.
        produced = sorted(Path(work_dir).glob("*.nii.gz"))
        if not produced:
            log("    [warn] no NIfTI files were produced for this subject.", log_lines)

        for nifti_path in produced:
            json_path = nifti_path.with_suffix("")          # drop .gz
            json_path = json_path.with_suffix(".json")      # -> .json
            info, sidecar = match_series_for_json(str(json_path), series_by_number)

            # Decide the destination category. If we couldn't match the series,
            # fall back to the description inside the JSON itself.
            if info is not None:
                description = info["description"]
                uid = info["uid"]
                number = info["number"]
                modality = info["modality"]
                sample_ds = info["sample_dataset"]
            else:
                description = (sidecar or {}).get("SeriesDescription", "")
                uid = "UNKNOWN"
                number = (sidecar or {}).get("SeriesNumber", "")
                modality = (sidecar or {}).get("Modality", "")
                sample_ds = None
                log(f"    [warn] could not match {nifti_path.name} to a series UID.",
                    log_lines)

            category = categorize_series(description)

            # Make sub-XXX/<category>/ and move BOTH the .nii.gz and its .json.
            dest_dir = os.path.join(output_dir, subject_label, category)
            os.makedirs(dest_dir, exist_ok=True)
            dest_nifti = os.path.join(dest_dir, nifti_path.name)
            shutil.move(str(nifti_path), dest_nifti)
            dest_json = ""
            if json_path.exists():
                dest_json = os.path.join(dest_dir, json_path.name)
                shutil.move(str(json_path), dest_json)

            log(f"    {nifti_path.name}  ->  {category}/  (series {number}: {description})",
                log_lines)

            # --- provenance: one row linking output back to the DICOM series.
            provenance_rows.append({
                "subject": subject_label,
                "series_uid": uid,
                "series_number": number,
                "series_description": description,
                "modality": modality,
                "category": category,
                "output_nifti": os.path.relpath(dest_nifti, output_dir),
                "output_json": os.path.relpath(dest_json, output_dir) if dest_json else "",
            })

            # --- validation: compare a few key DICOM vs JSON values.
            if sample_ds is not None and sidecar is not None:
                checks = [
                    ("SeriesDescription",
                     getattr(sample_ds, "SeriesDescription", ""),
                     sidecar.get("SeriesDescription", ""), 1.0),
                    # DICOM stores these in ms; JSON in seconds -> scale 0.001.
                    ("RepetitionTime",
                     getattr(sample_ds, "RepetitionTime", ""),
                     sidecar.get("RepetitionTime", ""), 0.001),
                    ("EchoTime",
                     getattr(sample_ds, "EchoTime", ""),
                     sidecar.get("EchoTime", ""), 0.001),
                ]
                for field, dicom_val, json_val, scale in checks:
                    status = compare_values(dicom_val, json_val, scale)
                    validation_rows.append({
                        "subject": subject_label,
                        "series_uid": uid,
                        "field": field,
                        "dicom_value": dicom_val,
                        "json_value": json_val,
                        "status": status,
                    })

    # --- write the two CSV reports ----------------------------------------
    provenance_csv = os.path.join(output_dir, "provenance.csv")
    with open(provenance_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "subject", "series_uid", "series_number", "series_description",
            "modality", "category", "output_nifti", "output_json"])
        writer.writeheader()
        writer.writerows(provenance_rows)

    validation_csv = os.path.join(output_dir, "validation_report.csv")
    with open(validation_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "subject", "series_uid", "field", "dicom_value",
            "json_value", "status"])
        writer.writeheader()
        writer.writerows(validation_rows)

    # --- clean up the temporary work folder -------------------------------
    # Everything useful has been moved out; remove the leftover empty area.
    shutil.rmtree(work_root, ignore_errors=True)

    # --- final summary -----------------------------------------------------
    n_mismatch = sum(1 for r in validation_rows if r["status"] == "MISMATCH")
    log("", log_lines)
    log("=" * 70, log_lines)
    log(f" DONE. Converted {len(provenance_rows)} series across "
        f"{len(subjects)} subject folder(s).", log_lines)
    log(f"   provenance      -> {provenance_csv}", log_lines)
    log(f"   validation      -> {validation_csv}  ({n_mismatch} mismatch flag(s))",
        log_lines)
    log("=" * 70, log_lines)

    # Write the full text log so problems can be reviewed later.
    with open(os.path.join(output_dir, "conversion_log.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(log_lines))


if __name__ == "__main__":
    main()
