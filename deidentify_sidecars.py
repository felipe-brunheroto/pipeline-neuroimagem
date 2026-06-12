"""
deidentify_sidecars.py
======================

PURPOSE
-------
dcm2niix's "-ba y" flag removes direct PATIENT identifiers from the BIDS JSON
sidecars (PatientName, PatientID, birth date, etc.), but it intentionally keeps
SITE and SCANNER metadata. For data shared under Open Science practices, the
recommended standard is the DICOM PS3.15 Basic Application-Level Confidentiality
Profile (the same standard OpenNeuro and the BIDS community follow), which also
removes institution and device identifiers, while RETAINING the acquisition
parameters needed for reproducibility and multi-site harmonization.

This script removes the identifying fields below from every *.json sidecar found
under OUTPUT_DIR, in place, and writes a de-identification log. It does NOT touch
the original DICOM files, the NIfTI images, or any acquisition parameters.

WHAT IS REMOVED (identifying)            WHAT IS KEPT (scientifically useful,
-----------------------------            non-identifying)
  InstitutionName                          Manufacturer, ManufacturersModelName
  InstitutionAddress                       MagneticFieldStrength, ImagingFrequency
  InstitutionalDepartmentName              SoftwareVersions
  StationName                              SeriesDescription, ProtocolName, StudyDescription
  DeviceSerialNumber                       all TR/TE/flip-angle/timing/coil params
  ProcedureStepDescription                 everything else

HOW TO RUN
----------
    python deidentify_sidecars.py
    python deidentify_sidecars.py "C:\\path\\to\\BIDS_output"
"""

import os
import sys
import json
from datetime import datetime

OUTPUT_DIR = r"C:\Users\felip\OneDrive\Documentos\RMs CBR\BIDS_output"

# Fields removed per the DICOM Basic Confidentiality Profile + CLAUDE.md rule 3.
FIELDS_TO_REMOVE = [
    "InstitutionName",
    "InstitutionAddress",
    "InstitutionalDepartmentName",
    "StationName",
    "DeviceSerialNumber",
    "ProcedureStepDescription",
]


def main():
    output_dir = sys.argv[1] if len(sys.argv) > 1 else OUTPUT_DIR
    if not os.path.isdir(output_dir):
        print(f"ERROR: output folder does not exist: {output_dir}")
        sys.exit(1)

    log_lines = []
    log_lines.append("De-identification of BIDS JSON sidecars")
    log_lines.append(f"run: {datetime.now().isoformat(timespec='seconds')}")
    log_lines.append("standard: DICOM PS3.15 Basic Application-Level "
                     "Confidentiality Profile (+ BIDS acquisition-metadata "
                     "retention)")
    log_lines.append("fields removed (values not recorded): "
                     + ", ".join(FIELDS_TO_REMOVE))
    log_lines.append("-" * 60)

    n_files = 0
    n_removed = 0
    # Per-field counter so we can confirm what was actually present.
    per_field = {f: 0 for f in FIELDS_TO_REMOVE}

    for root, _dirs, files in os.walk(output_dir):
        for name in files:
            if not name.lower().endswith(".json"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as err:
                log_lines.append(f"[skip] could not read {name}: {err}")
                continue

            removed_here = []
            for field in FIELDS_TO_REMOVE:
                if field in data:
                    del data[field]
                    removed_here.append(field)
                    per_field[field] += 1
                    n_removed += 1

            if removed_here:
                # Re-write with stable, human-readable formatting.
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent="\t", ensure_ascii=False)
                    f.write("\n")
                n_files += 1
                rel = os.path.relpath(path, output_dir)
                log_lines.append(f"{rel}: removed {', '.join(removed_here)}")

    log_lines.append("-" * 60)
    log_lines.append(f"sidecars modified: {n_files}")
    log_lines.append(f"total fields removed: {n_removed}")
    for field, count in per_field.items():
        log_lines.append(f"  {field}: removed from {count} sidecar(s)")

    log_path = os.path.join(output_dir, "deidentification_log.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

    print("\n".join(log_lines))
    print(f"\nlog written -> {log_path}")


if __name__ == "__main__":
    main()
