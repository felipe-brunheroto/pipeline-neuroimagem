"""
make_derivatives.py
===================

WHAT THIS IS (in plain language)
--------------------------------
make_bids.py curates the RAW data that BIDS validates cleanly (anatomical, and
functional when a task is known). This companion script handles everything else
that is NOT raw BIDS, placing each piece in the correct BIDS-aware location with
honest, transparent naming -- and never inventing values:

  1. bids/derivatives/scanner-derived/   <- scanner-DERIVED images
        ADC, TRACEW (diffusion-derived, no gradient table),
        SWI family (magnitude / phase / SWI / minIP),
        Perfusion_Weighted (scanner perfusion maps).
     These are real derivatives, so they get a derivatives dataset_description.json
     and `desc-` entities (e.g. ..._desc-ADC_dwi.nii.gz).

  2. bids/sourcedata/asl/                 <- raw ASL, PARTIALLY curated
        A draft _asl.json (fields we could read from the data filled in;
        everything we cannot know marked REQUIRED_FILL_ME), an _aslcontext.tsv
        template, and a README that says exactly what the researcher must
        supply. Nothing is fabricated. Facts a study declares about its own
        protocol (project.json "acquisition"; CBR: multi-TI 3D PASL, one file
        per inversion time, no M0) are filled in for that study only.

  3. bids/sourcedata/fmap/                <- field maps pending curation
        Copied with a README. CBR declares its field map phase-only (magnitude
        never acquired), so no valid BIDS fieldmap is possible from its data.

  4. bids/sourcedata/unknown/             <- the "new strangers"
        Any series no rule could classify, copied verbatim with a README pointing
        to the unknown-series report so they can be taught via series_map.csv.

It reads bids/sourcedata/conversion/provenance.csv (written by dicom_to_bids.py)
and never touches the original DICOMs. Re-runs are scoped: only the subtrees this
script owns (derivatives/scanner-derived + sourcedata/asl|fmap|unknown) are
rebuilt; the raw sub-*, the conversion source, and the FreeSurfer/defaced
derivatives are left untouched.

HOW TO RUN
----------
    python code/make_derivatives.py                # conversion -> bids
    python code/make_derivatives.py /path/conversion /path/bids
"""

import os
import re
import sys
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

# Reuse the shared helpers from make_bids.py (acq labels, entity parsing,
# de-identifying sidecar copy, JSON writer, identifier list). Importing the
# module is side-effect free (its main() only runs under __main__).
import make_bids as mb
import paths

BIDS_VERSION = mb.BIDS_VERSION


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent="\t", ensure_ascii=False)
        f.write("\n")


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _deid_sidecar_dict(src_json):
    """Load a sidecar and strip identifier fields (defence in depth)."""
    try:
        with open(src_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    for field in mb.IDENTIFIER_FIELDS:
        data.pop(field, None)
    return data


def _count_volumes(nifti_path):
    """Best-effort 4D volume count, so the ASL aslcontext template is the right
    length. Uses nibabel if available; otherwise returns None (the template then
    carries instructions instead of a fixed number of rows)."""
    try:
        import nibabel as nib
        img = nib.load(nifti_path)
        shape = img.shape
        return int(shape[3]) if len(shape) >= 4 else 1
    except Exception:
        return None


# ---------------------------------------------------------------------------
# derivatives/scanner-derived/
# ---------------------------------------------------------------------------

def _derived_suffix(row):
    """Pick a BIDS suffix for a derived image (never empty)."""
    suffix = (row.get("bids_suffix") or "").strip()
    if suffix:
        return suffix
    return {"dwi": "dwi", "swi": "swi", "perf": "asl"}.get(row["category"], "map")


def build_scanner_derived(rows, output_dir, deriv_dir):
    """Copy every derived image into derivatives/scanner-derived/ with desc-* names."""
    derived = [r for r in rows if r.get("derived") == "yes"]
    if not derived:
        return 0
    seen = {}
    n = 0
    for row in sorted(derived, key=lambda r: (r["subject"], r["session"],
                                              r["category"], r["series_number"])):
        subject, session = row["subject"], row["session"]
        acq = mb.acq_label(row["series_description"])
        ent = mb._parse_entities(row.get("bids_entities"))
        desc = ent.get("desc", "derived")
        suffix = _derived_suffix(row)
        anat_like_dir = os.path.join(deriv_dir, subject, session, row["category"])
        os.makedirs(anat_like_dir, exist_ok=True)
        base = f"{subject}_{session}_acq-{acq}_desc-{desc}"
        key = (base, suffix)
        seen[key] = seen.get(key, 0) + 1
        run = f"_run-{seen[key]:02d}" if seen[key] > 1 else ""
        stem = f"{base}{run}_{suffix}"

        src_nifti = os.path.join(output_dir, row["output_nifti"])
        if not os.path.isfile(src_nifti):
            print(f"  [warn] missing derived NIfTI: {row['output_nifti']}")
            continue
        shutil.copy2(src_nifti, os.path.join(anat_like_dir, stem + ".nii.gz"))
        src_json = os.path.join(output_dir, row.get("output_json") or "")
        if row.get("output_json") and os.path.isfile(src_json):
            write_json(os.path.join(anat_like_dir, stem + ".json"),
                       _deid_sidecar_dict(src_json))
        n += 1
        print(f"  derivatives/scanner-derived/{subject}/{session}/"
              f"{row['category']}/{stem}.nii.gz")

    write_json(os.path.join(deriv_dir, "dataset_description.json"), {
        "Name": f"{paths.PROJECT['title']} scanner-derived maps",
        "BIDSVersion": BIDS_VERSION,
        "DatasetType": "derivative",
        "GeneratedBy": [{
            "Name": "make_derivatives.py",
            "Description": "Scanner-produced derived images (ADC, trace-weighted "
                           "DWI, SWI magnitude/phase/SWI/minIP, perfusion-weighted "
                           "maps), copied and renamed with BIDS desc- entities. "
                           "These are NOT raw acquisitions.",
        }],
        "SourceDatasets": [{"Description": "Project conversion tree "
                            "(bids/sourcedata/conversion)."}],
    })
    return n


# ---------------------------------------------------------------------------
# sourcedata/asl/  -- transparent partial ASL curation
# ---------------------------------------------------------------------------

_ASL_TI_RE = re.compile(r"asl[_ ]?3d[_ ]?(\d+)", re.IGNORECASE)


# What a study declares about its own protocol (project.json "acquisition"):
# CBR's multi-TI 3D PASL without M0, its phase-only field map. A study that
# declares nothing gets REQUIRED_FILL_ME instead of another study's facts.
ACQUISITION = paths.PROJECT.get("acquisition", {})

GENERIC_ASL_README = (
    "Raw ASL -- partial BIDS curation pending researcher input\n"
    "=========================================================\n\n"
    "The files here are the raw ASL series of this study. This project.json\n"
    "declares nothing about its ASL protocol, so only what the sidecar itself\n"
    "carries is kept; everything else is marked REQUIRED_FILL_ME:\n"
    "  - ArterialSpinLabelingType (PASL / PCASL / CASL), M0Type\n"
    "  - PostLabelingDelay (and LabelingDuration for (P)CASL, the bolus\n"
    "    cut-off fields for PASL)\n"
    "  - BackgroundSuppression, TotalAcquiredPairs\n"
    "  - the per-volume order in each *_aslcontext.tsv\n"
    "    (control / label / m0scan / deltam / cbf)\n\n"
    "Facts that are always true of the protocol can be declared once under\n"
    "\"acquisition\" in project.json. Once confirmed, move the files into\n"
    "bids/<sub>/<ses>/perf/ to make a valid BIDS 'perf' datatype.\n")

GENERIC_FMAP_README = (
    "Field maps -- pending curation\n"
    "==============================\n\n"
    "The field-map series of this study, copied with de-identified sidecars.\n"
    "A valid BIDS fieldmap needs the right set of images (e.g. phasediff +\n"
    "magnitude1/2) and an IntendedFor list naming the scans it corrects; check\n"
    "which images were exported, then move them into bids/<sub>/<ses>/fmap/.\n")


def write_aslcontext(dest, stem, dest_nifti):
    """aslcontext.tsv template -- the volume order (control/label/m0scan) is
    NOT recorded in the headers, so the operator must confirm it."""
    n_vol = _count_volumes(dest_nifti)
    ctx_path = os.path.join(dest, stem + "context.tsv")
    with open(ctx_path, "w", newline="", encoding="utf-8") as f:
        f.write("volume_type\n")
        if n_vol:
            for _ in range(n_vol):
                f.write("REQUIRED_FILL_ME\n")   # control|label|m0scan|deltam|cbf
        else:
            f.write("# one row per volume: control|label|m0scan|deltam|cbf\n")


def build_asl_sourcedata(rows, output_dir, bids_dir):
    """Scaffold raw ASL under sourcedata/asl/, filling only what the data supports."""
    asl = [r for r in rows
           if r.get("category") == "perf" and r.get("derived") != "yes"]
    if not asl:
        return 0
    sd_dir = os.path.join(bids_dir, "sourcedata", "asl")
    n = 0
    for row in sorted(asl, key=lambda r: (r["subject"], r["session"],
                                          r["series_number"])):
        subject, session = row["subject"], row["session"]
        # The inversion time / PLD is only in the protocol name here.
        m = _ASL_TI_RE.search(row["series_description"] or "")
        ti_ms = m.group(1) if m else None
        inv = f"_inv-{ti_ms}" if ti_ms else ""
        dest = os.path.join(sd_dir, subject, session, "perf")
        os.makedirs(dest, exist_ok=True)
        acq = ACQUISITION.get("asl", {}).get("acq") or mb.acq_label(
            row["series_description"])
        stem = f"{subject}_{session}_acq-{acq}{inv}_asl"

        src_nifti = os.path.join(output_dir, row["output_nifti"])
        if not os.path.isfile(src_nifti):
            print(f"  [warn] missing ASL NIfTI: {row['output_nifti']}")
            continue
        dest_nifti = os.path.join(dest, stem + ".nii.gz")
        shutil.copy2(src_nifti, dest_nifti)

        # Draft sidecar: start from the (de-identified) acquisition sidecar, then
        # fill ONLY what we can justify from the data, and flag the rest.
        src_json = os.path.join(output_dir, row.get("output_json") or "")
        sidecar = _deid_sidecar_dict(src_json) if os.path.isfile(src_json) else {}
        facts = ACQUISITION.get("asl")
        if facts:
            sidecar.setdefault("ArterialSpinLabelingType", facts["ArterialSpinLabelingType"])
            sidecar.setdefault("MRAcquisitionType", facts["MRAcquisitionType"])
            sidecar["M0Type"] = facts["M0Type"]
        else:
            sidecar.setdefault("ArterialSpinLabelingType", "REQUIRED_FILL_ME")
            sidecar.setdefault("M0Type", "REQUIRED_FILL_ME")
        if sidecar["ArterialSpinLabelingType"] != "PASL":
            # (P)CASL or not known: the PASL-only inferences below do not apply.
            sidecar.setdefault("PostLabelingDelay", "REQUIRED_FILL_ME")
            sidecar.setdefault("LabelingDuration", "REQUIRED_FILL_ME")
            for field in ("BackgroundSuppression", "TotalAcquiredPairs"):
                sidecar.setdefault(field, "REQUIRED_FILL_ME")
            write_json(os.path.join(dest, stem + ".json"), sidecar)
            write_aslcontext(dest, stem, dest_nifti)
            n += 1
            print(f"  sourcedata/asl/{subject}/{session}/perf/{stem}.nii.gz")
            continue

        # PostLabelingDelay: prefer the sidecar's InversionTime (the PASL TI, in
        # seconds, exposed by dcm2niix); fall back to the value in the protocol
        # name. Always annotate where the number came from.
        inv_tag = sidecar.get("InversionTime")
        if isinstance(inv_tag, (int, float)) and inv_tag > 0:
            sidecar["PostLabelingDelay"] = round(float(inv_tag), 4)
            sidecar["PostLabelingDelaySource"] = (
                "from DICOM InversionTime (PASL TI); confirm with the sequence card")
        elif ti_ms:
            sidecar["PostLabelingDelay"] = round(int(ti_ms) / 1000.0, 4)
            sidecar["PostLabelingDelaySource"] = (
                f"parsed from protocol name 'asl_3d_{ti_ms}' (no DICOM timing "
                "tag); confirm with the sequence card")
        else:
            sidecar["PostLabelingDelay"] = "REQUIRED_FILL_ME"

        # Bolus cut-off: infer from the Siemens PASLType / BolusDuration the
        # sidecar carries (e.g. 'FAIR QII' -> QUIPSS II); flag if absent. The
        # source is recorded so the researcher can confirm the mapping.
        pasl_type = str(sidecar.get("PASLType", "")).lower()
        if pasl_type:
            sidecar["BolusCutOffFlag"] = True
            if "q2tips" in pasl_type:
                technique = "Q2TIPS"
            elif "qii" in pasl_type or "quipss" in pasl_type:
                technique = "QUIPSSII"
            else:
                technique = "REQUIRED_FILL_ME"
            sidecar["BolusCutOffTechnique"] = technique
            sidecar["BolusCutOffSource"] = (
                f"inferred from DICOM PASLType={sidecar.get('PASLType')!r}; confirm")
        else:
            sidecar["BolusCutOffFlag"] = "REQUIRED_FILL_ME"
            sidecar["BolusCutOffTechnique"] = "REQUIRED_FILL_ME"
        bolus = sidecar.get("BolusDuration")
        if isinstance(bolus, (int, float)) and bolus > 0:
            sidecar["BolusCutOffDelayTime"] = round(float(bolus), 4)
        else:
            sidecar["BolusCutOffDelayTime"] = "REQUIRED_FILL_ME"

        # Genuinely not in the DICOMs -> the researcher must supply these.
        for field in ("BackgroundSuppression", "TotalAcquiredPairs"):
            sidecar.setdefault(field, "REQUIRED_FILL_ME")
        write_json(os.path.join(dest, stem + ".json"), sidecar)
        write_aslcontext(dest, stem, dest_nifti)
        n += 1
        print(f"  sourcedata/asl/{subject}/{session}/perf/{stem}.nii.gz")

    if not ACQUISITION.get("asl"):
        write_text(os.path.join(sd_dir, "README"), GENERIC_ASL_README)
        return n
    write_text(os.path.join(sd_dir, "README"),
        "Raw ASL (PASL) -- partial BIDS curation pending researcher input\n"
        "================================================================\n\n"
        "This is a multi-inversion-time (multi-PLD) 3D PASL acquisition. The\n"
        "files here are the raw ASL series, one per inversion time (inv-<ms>).\n\n"
        "FILLED IN from the available data (each annotated with its source):\n"
        "  ArterialSpinLabelingType = PASL    (from the DICOM)\n"
        "  MRAcquisitionType        = 3D      (from the DICOM)\n"
        "  M0Type                   = Absent  (no M0 series was acquired)\n"
        "  PostLabelingDelay        = DICOM InversionTime (PASL TI), or the value\n"
        "                             in the protocol name when the tag is absent\n"
        "  BolusCutOffFlag/Technique= inferred from DICOM PASLType (e.g. FAIR QII\n"
        "                             -> QUIPSS II)\n"
        "  BolusCutOffDelayTime     = DICOM BolusDuration\n"
        "  (see *Source notes in each .json -- please CONFIRM these mappings)\n\n"
        "MUST BE SUPPLIED by the researcher (marked REQUIRED_FILL_ME), because it\n"
        "is not present in the DICOMs:\n"
        "  - BackgroundSuppression, TotalAcquiredPairs\n"
        "  - the per-volume order in each *_aslcontext.tsv\n"
        "    (control / label / m0scan / deltam / cbf)\n\n"
        "Once these are confirmed, move the files into bids/<sub>/<ses>/perf/ to\n"
        "make a valid BIDS 'perf' datatype. Nothing here was fabricated.\n")
    return n


# ---------------------------------------------------------------------------
# sourcedata/fmap/  -- field maps (CBR: phase only, not a valid BIDS fieldmap)
# ---------------------------------------------------------------------------

def build_fmap_sourcedata(rows, output_dir, bids_dir):
    """Copy the field map into sourcedata/fmap/ with an honest README."""
    fmap = [r for r in rows if r.get("category") == "fmap"]
    if not fmap:
        return 0
    sd_dir = os.path.join(bids_dir, "sourcedata", "fmap")
    n = 0
    for row in sorted(fmap, key=lambda r: (r["subject"], r["session"],
                                           r["series_number"])):
        subject, session = row["subject"], row["session"]
        acq = mb.acq_label(row["series_description"])
        dest = os.path.join(sd_dir, subject, session, "fmap")
        os.makedirs(dest, exist_ok=True)
        suffix = ("phase" if ACQUISITION.get("fmap") == "phase_only"
                  else row.get("bids_suffix") or "fmap")
        stem = f"{subject}_{session}_acq-{acq}_{suffix}"
        src_nifti = os.path.join(output_dir, row["output_nifti"])
        if os.path.isfile(src_nifti):
            shutil.copy2(src_nifti, os.path.join(dest, stem + ".nii.gz"))
        src_json = os.path.join(output_dir, row.get("output_json") or "")
        if row.get("output_json") and os.path.isfile(src_json):
            write_json(os.path.join(dest, stem + ".json"),
                       _deid_sidecar_dict(src_json))
        n += 1
        print(f"  sourcedata/fmap/{subject}/{session}/fmap/{stem}.nii.gz")

    if ACQUISITION.get("fmap") != "phase_only":
        write_text(os.path.join(sd_dir, "README"), GENERIC_FMAP_README)
        return n
    write_text(os.path.join(sd_dir, "README"),
        "Field map -- PHASE ONLY (not a valid BIDS fieldmap)\n"
        "===================================================\n\n"
        "The gre_field_mapping series here exported only the PHASE image; the\n"
        "MAGNITUDE images were never acquired (verified in the original DICOM).\n"
        "Every BIDS fieldmap type requires at least one magnitude image, so a\n"
        "valid BIDS 'fmap' cannot be produced from this data. The phase image is\n"
        "kept here for completeness/provenance.\n\n"
        "If a future project acquires both magnitude and phase, add series_map.csv\n"
        "rows for them and curate them into bids/<sub>/<ses>/fmap/ with the\n"
        "appropriate suffixes (phasediff + magnitude1/2) and an IntendedFor list.\n")
    return n


# ---------------------------------------------------------------------------
# sourcedata/unknown/  -- the new strangers
# ---------------------------------------------------------------------------

def build_unknown_sourcedata(rows, output_dir, bids_dir):
    """Copy unclassified series verbatim into sourcedata/unknown/ + a README."""
    unknown = [r for r in rows if r.get("category") == "unknown"]
    if not unknown:
        return 0
    sd_dir = os.path.join(bids_dir, "sourcedata", "unknown")
    n = 0
    for row in unknown:
        subject, session = row["subject"], row["session"]
        dest = os.path.join(sd_dir, subject, session)
        os.makedirs(dest, exist_ok=True)
        for key in ("output_nifti", "output_json"):
            rel = row.get(key) or ""
            src = os.path.join(output_dir, rel)
            if rel and os.path.isfile(src):
                shutil.copy2(src, os.path.join(dest, os.path.basename(rel)))
        n += 1
        print(f"  sourcedata/unknown/{subject}/{session}/"
              f"{os.path.basename(row.get('output_nifti') or '')}")

    write_text(os.path.join(sd_dir, "README"),
        "Unrecognized series (the 'new strangers')\n"
        "=========================================\n\n"
        "These series matched no rule in series_map.csv and no built-in keyword,\n"
        "so they were quarantined here instead of being misfiled. See\n"
        "reports/unknown_series_report.csv (next to provenance.csv) for the DICOM\n"
        "hints, and reports/series_map_additions.csv for ready-to-edit rows.\n\n"
        "To classify them: fill in the datatype/suffix in those stub rows, paste\n"
        "them into series_map.csv, and re-run the pipeline. Nothing is lost.\n")
    return n


def _scoped_clean(bids_dir, rows):
    """Remove only the subtrees THIS script owns, so a re-run is deterministic
    without disturbing make_bids' raw sub-*, the FreeSurfer/defaced derivatives,
    or the conversion source. In scanner-derived/ that means only the sessions
    listed in provenance.csv: sessions brought in by import_bids.py are kept.
    Safe if the folders do not exist yet."""
    for subject, session in {(r["subject"], r["session"]) for r in rows}:
        shutil.rmtree(os.path.join(bids_dir, "derivatives", "scanner-derived",
                                   subject, session), ignore_errors=True)
    for name in ("asl", "fmap", "unknown"):
        shutil.rmtree(os.path.join(bids_dir, "sourcedata", name),
                      ignore_errors=True)


def main():
    output_dir = sys.argv[1] if len(sys.argv) > 1 else str(paths.CONVERSION_DIR)
    bids_dir = sys.argv[2] if len(sys.argv) > 2 else str(paths.BIDS_ROOT)
    if not os.path.isdir(output_dir):
        print(f"ERROR: input folder does not exist: {output_dir}")
        print(f"  Run dicom_to_bids.py first (writes {paths.CONVERSION_DIR}).")
        sys.exit(1)

    rows = mb.load_provenance(output_dir)
    _scoped_clean(bids_dir, rows)
    deriv_dir = os.path.join(bids_dir, "derivatives", "scanner-derived")

    print("=" * 70)
    print(" Build derivatives + sourcedata scaffolds")
    print(f" provenance : {os.path.join(output_dir, 'provenance.csv')}")
    print(f" bids root  : {bids_dir}")
    print("=" * 70)

    n_deriv = build_scanner_derived(rows, output_dir, deriv_dir)
    n_asl = build_asl_sourcedata(rows, output_dir, bids_dir)
    n_fmap = build_fmap_sourcedata(rows, output_dir, bids_dir)
    n_unknown = build_unknown_sourcedata(rows, output_dir, bids_dir)

    print("")
    print("=" * 70)
    print(f" DONE. scanner-derived={n_deriv}  asl(sourcedata)={n_asl}  "
          f"fmap(sourcedata)={n_fmap}  unknown(sourcedata)={n_unknown}")
    print(f"   derivatives -> {deriv_dir}")
    print(f"   sourcedata  -> {os.path.join(bids_dir, 'sourcedata')}")
    print("=" * 70)


if __name__ == "__main__":
    main()
