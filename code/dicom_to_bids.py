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
import re
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

# Local helper modules (same folder). series_classifier holds the shared,
# data-driven rules that decide each series' datatype + BIDS suffix; it also
# reads the operator-editable series_map.csv. setup_workspace prepares/validates
# the input folder so data is never misdirected on the first run.
import series_classifier as sc
import session_map
import setup_workspace
import paths


# ============================================================================
# 1. CONFIGURATION  -  edit these two paths for your computer if you like.
# ============================================================================
# INPUT_DIR  : the folder that CONTAINS your subject folders (e.g. CBR-003).
# OUTPUT_DIR : where the converted, organized results will be written -- the
#              working/pre-BIDS "conversion" tree, kept under the dataset's
#              sourcedata/ (see paths.py). It is SEPARATE from the input so your
#              originals are never touched.
#
# The defaults come from paths.py (the single source of truth for the project
# layout), so the pipeline runs as-is on any machine without editing paths. You
# can still override both on the command line (see below).
_HERE = Path(__file__).resolve().parent
INPUT_DIR  = str(paths.PATIENT_FILES)    # subject folders live in here
OUTPUT_DIR = str(paths.CONVERSION_DIR)   # results are written here


# ============================================================================
# 2. SERIES CLASSIFICATION
# ============================================================================
# The rules that decide which datatype folder a series goes in (anat / func /
# dwi / fmap / perf / swi / misc / unknown) -- and its BIDS suffix/entities --
# now live in ONE shared place: series_classifier.py. That module first consults
# the operator-editable series_map.csv (so a NEW project, e.g. a future BOLD
# study, is taught by editing one CSV, not the code), then falls back to built-in
# keyword rules. Anything neither layer recognizes comes back as "unknown" and is
# quarantined + reported below rather than silently mis-sorted.


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


# Optional explicit overrides for non-CBR projects: a CSV mapping a raw folder
# name to a chosen subject/session, for full control when a project's naming is
# unusual. Missing file -> no overrides (the patterns below decide).
SUBJECTS_MAP_PATH = str(_HERE / "subjects_map.csv")

# The project's own CBR convention, kept FIRST so existing labels are unchanged:
# subject digits, then an OPTIONAL visit number after '.', '-', '_' or space.
# e.g. 'CBR-003-2', 'CBR-003.3', 'CBR-052'.
CBR_RE = re.compile(r"CBR[-_ ]?(\d+)(?:[.\-_ ](\d+))?", re.IGNORECASE)

# Generic fallback: a folder ending in '.N' is treated as visit N (a dot suffix
# is a strong, cross-project visit signal); anything else becomes ses-1. We do
# NOT split on '-'/'_' generically, since those are usually part of an ID
# (e.g. 'STUDY2-07'); use subjects_map.csv for precise control of such cases.
_GENERIC_VISIT_RE = re.compile(r"^(.*\S)\.(\d+)\s*$")


def load_subjects_map(path=SUBJECTS_MAP_PATH):
    """folder name -> (subject_label, session_label) explicit overrides."""
    out = {}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, newline="", encoding="utf-8") as f:
            lines = [ln for ln in f
                     if ln.strip() and not ln.lstrip().startswith("#")]
        for row in csv.DictReader(lines):
            row = {(k or "").strip().lower(): (v or "").strip()
                   for k, v in row.items()}
            folder = row.get("folder", "")
            sub = row.get("subject", "")
            ses = row.get("session", "") or "ses-1"
            if not folder or not sub:
                continue
            if not sub.startswith("sub-"):
                sub = "sub-" + "".join(c for c in sub if c.isalnum())
            if not ses.startswith("ses-"):
                ses = "ses-" + "".join(c for c in ses if c.isalnum())
            out[folder] = (sub, ses)
    except Exception:
        return {}
    return out


def parse_subject_session(folder_name, subjects_map=None):
    """
    Turn a raw subject-folder name into a (subject_label, session_label) pair.

    Decision order, so existing CBR labels never change AND a brand-new project's
    folders still get a valid BIDS label instead of being dropped:
      1. subjects_map.csv explicit override (if the folder is listed).
      2. CBR convention (CBR_RE): 'CBR-003.3' -> ('sub-CBR003','ses-3'),
         'CBR-052' -> ('sub-CBR052','ses-1'). The suffix number IS the visit
         index (kept, not renumbered, so a 6+12-month-only subject reads
         ses-2/ses-3 correctly), and we use the index (not the date) to stay
         de-identified (CLAUDE.md rule 3).
      3. Generic fallback: a trailing '.N' -> visit N; otherwise the whole
         (alnum-cleaned) name -> 'sub-<name>', ses-1.

    Examples (non-CBR):
        'STUDY2-07'  -> ('sub-STUDY207', 'ses-1')
        'SUBJ01.2'   -> ('sub-SUBJ01',   'ses-2')
    """
    name = folder_name or ""
    if subjects_map and name in subjects_map:
        return subjects_map[name]
    match = CBR_RE.search(name)
    if match:
        visit = match.group(2)
        return "sub-CBR" + match.group(1), "ses-" + (visit if visit else "1")
    gv = _GENERIC_VISIT_RE.match(name)
    if gv:
        return safe_label(gv.group(1)), "ses-" + gv.group(2)
    return safe_label(name), "ses-1"


def project_subject_label(folder_name):
    """
    For projects that declare a "subject_pattern" (e.g. COGAGING: 'GER\\d+'),
    the subject code is the pattern found ANYWHERE in the exam folder name, so
    'sub-GER00039_SOMETHING' -> 'sub-GER00039'. Everything else in the folder
    name is ignored (it may hold a patient name). None if the code is missing.
    """
    match = re.search(paths.PROJECT["subject_pattern"], folder_name or "", re.IGNORECASE)
    return "sub-" + match.group(0).upper() if match else None


def first_study_info(exam_dir):
    """(StudyDate, StudyInstanceUID) of the first DICOM found in an exam folder."""
    for root, _dirs, files in os.walk(exam_dir):
        for filename in sorted(files):
            path = os.path.join(root, filename)
            if not is_dicom_file(path):
                continue
            try:
                ds = pydicom.dcmread(path, stop_before_pixels=True, force=True,
                                     specific_tags=["StudyDate", "StudyInstanceUID"])
            except Exception:
                continue
            return (str(getattr(ds, "StudyDate", "") or ""),
                    str(getattr(ds, "StudyInstanceUID", "") or ""))
    return "", ""


def scan_subject_series(subject_dir, log_lines):
    """
    Walk through one subject's folder, find all DICOM files, and group them by
    SeriesInstanceUID.

    Returns a dictionary:
        { series_uid : {
              "number": SeriesNumber,
              "description": SeriesDescription,
              "modality": Modality,
              "image_type": ImageType joined to a string (signal for BOLD/ASL),
              "sequence": SequenceName (extra hint for the unknown-series report),
              "sample_dataset": <one pydicom dataset, used later for validation>,
              "file_count": how many DICOM files were in this series
          }, ... }

    We only read the few tags we need (specific_tags) so this stays fast even
    with thousands of files. ImageType / SequenceName are read so the shared
    classifier can spot functional (BOLD) runs even when the name is unfamiliar,
    and so the unknown-series report carries useful hints.
    """
    # Tags we actually need: identity of the series + validation + classification.
    wanted_tags = ["SeriesInstanceUID", "SeriesNumber", "SeriesDescription",
                   "Modality", "RepetitionTime", "EchoTime", "ProtocolName",
                   "ImageType", "SequenceName"]

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
                log(f"    [skip] could not read DICOM "
                    f"'{os.path.relpath(path, subject_dir)}': {err}", log_lines)
                continue

            uid = getattr(ds, "SeriesInstanceUID", None)
            if not uid:
                # Without a series UID we cannot group reliably -> log and skip.
                log(f"    [skip] no SeriesInstanceUID in "
                    f"'{os.path.relpath(path, subject_dir)}'", log_lines)
                continue

            n_dicom += 1
            if uid not in series:
                image_type = getattr(ds, "ImageType", "") or ""
                # pydicom returns a MultiValue (not a list) for multi-valued tags.
                if not isinstance(image_type, str):
                    image_type = "/".join(str(x) for x in image_type)
                series[uid] = {
                    "number": getattr(ds, "SeriesNumber", ""),
                    "description": getattr(ds, "SeriesDescription", "") or "",
                    "modality": getattr(ds, "Modality", "") or "",
                    "image_type": str(image_type),
                    "sequence": str(getattr(ds, "SequenceName", "") or ""),
                    "sample_dataset": ds,   # keep one example for validation
                    "file_count": 0,
                }
            series[uid]["file_count"] += 1

    log(f"    found {n_dicom} DICOM files in {len(series)} series", log_lines)
    return series


def run_dcm2niix(dcm2niix_path, subject_dir, work_dir, file_prefix, log_lines):
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
    file_pattern = file_prefix + "_series%s_%d"
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
    # Projects that read the study code out of the folder name never write the
    # folder name itself (it may carry a patient name) into the log.
    hide = (lambda text: text.replace(str(subject_dir), "<exam folder>")) \
        if paths.PROJECT.get("subject_pattern") else (lambda text: text)
    log(hide(f"    running: {' '.join(command)}"), log_lines)
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=3600)  # 1 hour safety limit
        # dcm2niix prints lots of progress text; keep the last few lines.
        output = (result.stdout + result.stderr).strip().splitlines()
        for line in output[-8:]:
            log(hide(f"      dcm2niix> {line}"), log_lines)
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

    # --- check the input folder before doing anything ----------------------
    # If the folder is missing entirely, create it (with a README) and stop so
    # the user can drop their exam folders in -- no misdirected run from the
    # get-go. If it exists, diagnose it and STOP on the common mistakes
    # (pointing inside a single exam, or an empty folder) before converting.
    if not os.path.isdir(input_dir):
        log(f"Input folder did not exist; creating it now:", log_lines)
        created_path, _ = setup_workspace.ensure_input_folder(
            root=os.path.dirname(os.path.abspath(input_dir)) or ".",
            input_name=os.path.basename(os.path.normpath(input_dir)))
        log(f"  {created_path}", log_lines)
        log("  Drop ONE FOLDER PER EXAM inside it, then run this again.", log_lines)
        log("  (See the README placed in that folder.)", log_lines)
        sys.exit(1)

    state = setup_workspace.diagnose_data_folder(input_dir)
    if state != "ok":
        log("", log_lines)
        log("Conversion stopped: the input folder does not look right (see above).",
            log_lines)
        log("Fix where the exam folders are, then run again.", log_lines)
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

    # Load the classification rules once: the operator-editable series_map.csv
    # (teaches new projects) and any subjects_map.csv (explicit folder->labels).
    # The study's own rules (projects/<NAME>/series_map.csv) come first, so one
    # study's scanner names never change another's; then the shared map.
    series_map = (sc.load_series_map(str(paths.PROJECT_SERIES_MAP))
                  + sc.load_series_map())
    subjects_map = load_subjects_map()
    log(f" series_map rules: {len(series_map)}   "
        f"subjects_map overrides: {len(subjects_map)}", log_lines)

    # These lists collect rows for the reports.
    provenance_rows = []   # one row per produced NIfTI (now incl. BIDS hints)
    validation_rows = []   # subject, series_uid, field, dicom_value, json_value, status
    unknown_rows = []      # series no rule could classify (the "new strangers")

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

    # --- subject / session labels -----------------------------------------
    # Projects with a "subject_pattern" (COGAGING) take the study code from the
    # folder name and number sessions by exam date (session_map.assign); the
    # rest of the folder name is never written anywhere. Other projects (CBR)
    # use parse_subject_session (folder suffix = visit index).
    labels = {}
    if paths.PROJECT.get("subject_pattern"):
        exams = []
        for k, (subject_name, subject_dir) in enumerate(subjects, start=1):
            subject_label = project_subject_label(subject_name)
            if subject_label is None:
                log(f"ERROR: exam folder #{k} has no study code matching "
                    f"{paths.PROJECT['subject_pattern']!r} in its name; rename it "
                    "(e.g. GER00039_...) and run again.", log_lines)
                sys.exit(1)
            date, uid = first_study_info(subject_dir)
            exams.append((subject_name, subject_label, date, uid))
        if paths.PROJECT.get("sessions") == "date_order":
            labels, problems = session_map.assign(exams, str(paths.SESSION_MAP))
            if problems:
                for p in problems:
                    log(f"ERROR: {p}", log_lines)
                log("Nothing was converted; session numbers already in use were "
                    "not changed.", log_lines)
                sys.exit(1)
        else:
            labels = {e[0]: (e[1], "ses-1") for e in exams}
    else:
        labels = {name: parse_subject_session(name, subjects_map)
                  for name, _dir in subjects}

    # --- process each subject ---------------------------------------------
    for k, (subject_name, subject_dir) in enumerate(subjects, start=1):
        # One folder = one subject + one visit/session. Repeat visits of the
        # same person (e.g. CBR-003-2 and CBR-003.3) share the subject_label and
        # only differ in session_label, so they nest under one sub-XXX folder.
        subject_label, session_label = labels[subject_name]
        file_prefix = f"{subject_label}_{session_label}"
        shown = f"exam folder #{k}" if paths.PROJECT.get("subject_pattern") else subject_name
        log("", log_lines)
        log(f"SUBJECT: {shown}  ->  {subject_label}/{session_label}", log_lines)

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

        # 2) Convert with dcm2niix into a temporary work folder that is unique
        #    PER VISIT (file_prefix includes the session, so two visits of the
        #    same subject never collide in the work area).
        work_dir = os.path.join(work_root, file_prefix)
        os.makedirs(work_dir, exist_ok=True)
        ok = run_dcm2niix(dcm2niix_path, subject_dir, work_dir,
                          file_prefix, log_lines)
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
                image_type = info.get("image_type", "")
                sequence = info.get("sequence", "")
                file_count = info.get("file_count", "")
                sample_ds = info["sample_dataset"]
            else:
                description = (sidecar or {}).get("SeriesDescription", "")
                uid = "UNKNOWN"
                number = (sidecar or {}).get("SeriesNumber", "")
                modality = (sidecar or {}).get("Modality", "")
                it = (sidecar or {}).get("ImageType", "")
                image_type = "/".join(it) if isinstance(it, list) else str(it)
                sequence = (sidecar or {}).get("SequenceName", "")
                file_count = ""
                sample_ds = None
                log(f"    [warn] could not match {nifti_path.name} to a series UID.",
                    log_lines)

            # Classify with the shared rules (series_map.csv first, then built-in
            # keywords, then ImageType for unnamed BOLD). Unknown -> quarantine.
            cls = sc.classify(description, image_type=image_type,
                              modality=modality, sequence=sequence,
                              series_map=series_map)
            category = cls.datatype
            entities_text = ";".join(f"{k}={v}" for k, v in cls.entities.items())

            if category == "unknown":
                log(f"    [unknown] {nifti_path.name}: no rule matched "
                    f"(series {number}: {description!r}) -> quarantined in "
                    "unknown/ (promoted to bids/sourcedata/unknown/ by "
                    "make_derivatives.py)", log_lines)
                unknown_rows.append({
                    "subject": subject_label,
                    "session": session_label,
                    "series_number": number,
                    "series_description": description,
                    "modality": modality,
                    "image_type": image_type,
                    "sequence": sequence,
                    "dicom_file_count": file_count,
                    "suggestion": sc.suggest_for_unknown(description, image_type),
                })

            # Make sub-XXX/ses-Y/<category>/ and move BOTH the .nii.gz and json.
            dest_dir = os.path.join(output_dir, subject_label, session_label,
                                    category)
            os.makedirs(dest_dir, exist_ok=True)
            dest_nifti = os.path.join(dest_dir, nifti_path.name)
            shutil.move(str(nifti_path), dest_nifti)
            dest_json = ""
            if json_path.exists():
                dest_json = os.path.join(dest_dir, json_path.name)
                shutil.move(str(json_path), dest_json)

            log(f"    {nifti_path.name}  ->  {category}/  (series {number}: {description})",
                log_lines)

            # --- provenance: one row linking output back to the DICOM series,
            #     now carrying the BIDS classification so make_bids.py /
            #     make_derivatives.py can name files without re-deriving anything.
            provenance_rows.append({
                "subject": subject_label,
                "session": session_label,
                "series_uid": uid,
                "series_number": number,
                "series_description": description,
                "modality": modality,
                "category": category,
                "bids_suffix": cls.suffix,
                "bids_entities": entities_text,
                "derived": "yes" if cls.derived else "",
                "classifier_source": cls.source,
                "image_type": image_type,
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
                        "session": session_label,
                        "series_uid": uid,
                        "field": field,
                        "dicom_value": dicom_val,
                        "json_value": json_val,
                        "status": status,
                    })

    # --- write the reports ------------------------------------------------
    provenance_csv = os.path.join(output_dir, "provenance.csv")
    with open(provenance_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "subject", "session", "series_uid", "series_number",
            "series_description", "modality", "category",
            "bids_suffix", "bids_entities", "derived", "classifier_source",
            "image_type", "output_nifti", "output_json"])
        writer.writeheader()
        writer.writerows(provenance_rows)

    validation_csv = os.path.join(output_dir, "validation_report.csv")
    with open(validation_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "subject", "session", "series_uid", "field", "dicom_value",
            "json_value", "status"])
        writer.writeheader()
        writer.writerows(validation_rows)

    # --- reports/: unknown series + a ready-to-edit map + readiness summary -
    reports_dir = os.path.join(output_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    # 1) Every series no rule could classify, with the hints needed to decide.
    unknown_csv = os.path.join(reports_dir, "unknown_series_report.csv")
    with open(unknown_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "subject", "session", "series_number", "series_description",
            "modality", "image_type", "sequence", "dicom_file_count",
            "suggestion"])
        writer.writeheader()
        writer.writerows(unknown_rows)

    # 2) Ready-to-edit stub rows for series_map.csv (one per distinct unknown
    #    description). The operator fills in 'datatype'/'suffix' and pastes the
    #    rows into series_map.csv -- we do NOT mutate the live map automatically.
    additions_csv = os.path.join(reports_dir, "series_map_additions.csv")
    seen_desc = []
    with open(additions_csv, "w", newline="", encoding="utf-8") as f:
        f.write("# Fill in 'datatype' (and optionally suffix/entities/derived),\n"
                "# then paste these rows into series_map.csv and re-run.\n")
        writer = csv.DictWriter(f, fieldnames=[
            "match_type", "pattern", "datatype", "suffix", "entities",
            "derived", "notes"])
        writer.writeheader()
        for row in unknown_rows:
            desc = row["series_description"]
            if not desc or desc in seen_desc:
                continue
            seen_desc.append(desc)
            writer.writerow({
                "match_type": "exact",
                "pattern": desc,
                "datatype": "",          # <- operator fills this in
                "suffix": "",
                "entities": "",
                "derived": "",
                "notes": row["suggestion"],
            })

    # 3) A plain-English readiness summary: what is classified, what needs you.
    by_category = {}
    by_source = {}
    for r in provenance_rows:
        by_category[r["category"]] = by_category.get(r["category"], 0) + 1
        by_source[r["classifier_source"]] = by_source.get(r["classifier_source"], 0) + 1
    readiness_path = os.path.join(reports_dir, "bids_readiness_report.txt")
    rlines = []
    rlines.append("BIDS readiness report")
    rlines.append(f"generated: {datetime.now().isoformat(timespec='seconds')}")
    rlines.append(f"input: {input_dir}")
    rlines.append("-" * 60)
    rlines.append(f"series converted: {len(provenance_rows)} across "
                  f"{len(subjects)} folder(s)")
    rlines.append("by datatype:")
    for cat in sorted(by_category):
        rlines.append(f"    {cat:<10} {by_category[cat]}")
    rlines.append("by classifier source:")
    for src in sorted(by_source):
        rlines.append(f"    {src:<14} {by_source[src]}")
    rlines.append("-" * 60)
    if unknown_rows:
        rlines.append(f"UNKNOWN series needing your input: {len(unknown_rows)}")
        rlines.append(f"  -> see {os.path.relpath(unknown_csv, output_dir)}")
        rlines.append(f"  -> edit {os.path.relpath(additions_csv, output_dir)} "
                      "and paste into series_map.csv, then re-run.")
    else:
        rlines.append("UNKNOWN series: none -- every series was classified.")
    rlines.append("")
    rlines.append("Next steps to full BIDS: run deidentify_sidecars.py, then")
    rlines.append("make_bids.py (raw anat/func) and make_derivatives.py")
    rlines.append("(scanner-derived maps + ASL/fmap sourcedata scaffolds).")
    with open(readiness_path, "w", encoding="utf-8") as f:
        f.write("\n".join(rlines) + "\n")

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
    if unknown_rows:
        log(f"   UNKNOWN series  -> {len(unknown_rows)} flagged; see "
            f"{os.path.relpath(readiness_path, output_dir)}", log_lines)
    log("=" * 70, log_lines)

    # Write the full text log so problems can be reviewed later.
    with open(os.path.join(output_dir, "conversion_log.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(log_lines))


if __name__ == "__main__":
    main()
