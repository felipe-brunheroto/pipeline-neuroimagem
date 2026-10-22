"""
export_openneuro.py
===================

PURPOSE
-------
Make sharing the dataset on an open-science platform (OpenNeuro) easy and safe.
This is a LOCAL helper -- it never uploads anything itself. It:

  1. PRE-FLIGHT CHECKS the raw BIDS at the project's bids/ root:
       - dataset_description.json and participants.tsv present;
       - at least one sub-* folder;
       - no identifier fields left in any RAW sidecar (defence in depth on top of
         deidentify_sidecars.py / make_bids.py);
       - no leftover REQUIRED_FILL_ME placeholders in RAW sidecars.
     (sourcedata/ and derivatives/ are NOT failed on -- sourcedata is your private
     working tree and derivatives carry their own dataset_description.json.)

  2. PRINTS (and, with --validate, runs) the official BIDS validator command.

  3. PRINTS an upload checklist -- what to include, and the reminder that
     bids/sourcedata/conversion/ is your private working tree to leave out.

  4. With --assemble, builds a slim, upload-ready COPY under
     deliverable/bids_openneuro/ containing only the raw dataset (sub-*, the
     top-level metadata) -- and, with --with-derivatives, the derivatives/ too --
     so you can drag-and-drop it, leaving the working tree behind. No data leaves
     the machine.

HOW TO RUN
----------
    python code/export_openneuro.py                 # checks + checklist
    python code/export_openneuro.py --validate      # also run the BIDS validator
    python code/export_openneuro.py --assemble      # build deliverable/bids_openneuro/
    python code/export_openneuro.py --assemble --with-derivatives
"""

import os
import sys
import json
import glob
import shutil
import argparse
import subprocess
from pathlib import Path

import paths
import make_bids as mb   # reuse the canonical IDENTIFIER_FIELDS list


# Top-level files that belong to the RAW dataset (everything else at the root that
# is not sub-*/sourcedata/derivatives is study metadata we carry along).
RAW_TOP_LEVEL = [
    "dataset_description.json", "README", "CHANGES",
    "participants.tsv", "participants.json", "sessions.json", ".bidsignore",
]


def _raw_sidecars(bids_root):
    """Every JSON sidecar under the RAW sub-* tree (not sourcedata/derivatives)."""
    return [Path(p) for p in glob.glob(os.path.join(bids_root, "sub-*", "**",
                                                    "*.json"), recursive=True)]


def preflight(bids_root):
    """Return (ok, problems, notes). Never raises on a normal dataset."""
    problems, notes = [], []
    bids_root = Path(bids_root)

    if not bids_root.is_dir():
        return False, [f"bids/ root not found: {bids_root}"], notes

    if not (bids_root / "dataset_description.json").is_file():
        problems.append("missing dataset_description.json (run make_bids.py)")
    if not (bids_root / "participants.tsv").is_file():
        problems.append("missing participants.tsv (run make_bids.py)")

    subjects = sorted(p.name for p in bids_root.glob("sub-*") if p.is_dir())
    if not subjects:
        problems.append("no sub-* folders found (run make_bids.py)")
    else:
        notes.append(f"{len(subjects)} subject(s): {', '.join(subjects)}")

    # Scan raw sidecars for identifiers + unfilled placeholders.
    id_hits, fillme_hits = [], []
    sidecars = _raw_sidecars(bids_root)
    for sc in sidecars:
        try:
            with open(sc, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as err:
            problems.append(f"could not read sidecar {sc.name}: {err}")
            continue
        present = [field for field in mb.IDENTIFIER_FIELDS if field in data]
        if present:
            id_hits.append((sc, present))
        if any(v == "REQUIRED_FILL_ME" for v in data.values()):
            fillme_hits.append(sc)
    notes.append(f"{len(sidecars)} raw sidecar(s) scanned")

    rel = lambda p: os.path.relpath(p, bids_root)
    for sc, present in id_hits:
        problems.append(f"identifier field(s) {present} in raw sidecar {rel(sc)} "
                        "(re-run deidentify_sidecars.py)")
    for sc in fillme_hits:
        problems.append(f"unfilled REQUIRED_FILL_ME in raw sidecar {rel(sc)}")

    return (not problems), problems, notes


def validator_command(bids_root):
    """Pick an available BIDS validator invocation; return (cmd_list, label)."""
    if shutil.which("deno"):
        return (["deno", "run", "-A", "jsr:@bids/validator", str(bids_root)],
                "deno run -A jsr:@bids/validator")
    if shutil.which("npx"):
        return (["npx", "@bids/validator", str(bids_root)], "npx @bids/validator")
    if shutil.which("bids-validator"):
        return (["bids-validator", str(bids_root)], "bids-validator")
    return (None, None)


def assemble(bids_root, dest, with_derivatives):
    """Copy a slim, upload-ready raw dataset (optionally + derivatives) to dest.
    Always EXCLUDES sourcedata/ (the private working tree)."""
    bids_root = Path(bids_root)
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    n = 0
    for name in RAW_TOP_LEVEL:
        src = bids_root / name
        if src.is_file():
            shutil.copy2(src, dest / name)
            n += 1
    for events in bids_root.glob("task-*_events.json"):
        shutil.copy2(events, dest / events.name)
        n += 1
    for sub in sorted(bids_root.glob("sub-*")):
        if sub.is_dir():
            shutil.copytree(sub, dest / sub.name)
            n += 1
    if with_derivatives and (bids_root / "derivatives").is_dir():
        shutil.copytree(bids_root / "derivatives", dest / "derivatives")
        n += 1
    return n


def main():
    parser = argparse.ArgumentParser(
        description="Pre-flight + assemble the bids/ dataset for open-science upload.")
    parser.add_argument("bids_root", nargs="?", default=str(paths.BIDS_ROOT),
                        help="BIDS dataset root (default: the project's bids/).")
    parser.add_argument("--validate", action="store_true",
                        help="Run the BIDS validator if one is installed.")
    parser.add_argument("--assemble", action="store_true",
                        help="Copy a slim raw-only dataset to deliverable/bids_openneuro/.")
    parser.add_argument("--with-derivatives", action="store_true",
                        help="Include derivatives/ in the assembled copy.")
    parser.add_argument("--dest", default=str(paths.DELIVERABLE_DIR / "bids_openneuro"),
                        help="Where --assemble writes (default: deliverable/bids_openneuro).")
    args = parser.parse_args()

    print("=" * 70)
    print(" OpenNeuro export helper (local-only; nothing is uploaded)")
    print(f" dataset: {args.bids_root}")
    print("=" * 70)

    ok, problems, notes = preflight(args.bids_root)
    for note in notes:
        print(f"  - {note}")
    print("-" * 70)
    if ok:
        print(" PRE-FLIGHT: OK -- raw dataset looks ready.")
    else:
        print(" PRE-FLIGHT: issues to fix before sharing:")
        for p in problems:
            print(f"   [x] {p}")

    # Validator
    cmd, label = validator_command(args.bids_root)
    print("-" * 70)
    if cmd and args.validate:
        print(f" Running validator: {label} {args.bids_root}")
        try:
            rc = subprocess.run(cmd).returncode
            print(f" validator exit code: {rc}")
        except Exception as err:
            print(f" [warn] could not run validator: {err}")
    elif cmd:
        print(" To validate (recommended), run:")
        print(f"   {label} {args.bids_root}")
        print(" (or re-run this with --validate)")
    else:
        print(" BIDS validator not found. Install one, then validate:")
        print(f"   deno run -A jsr:@bids/validator {args.bids_root}")
        print(f"   npx @bids/validator {args.bids_root}")

    # Assemble
    if args.assemble:
        print("-" * 70)
        if not ok:
            print(" --assemble skipped: fix the pre-flight issues above first.")
        else:
            n = assemble(args.bids_root, args.dest, args.with_derivatives)
            kind = "raw + derivatives" if args.with_derivatives else "raw only"
            print(f" Assembled {kind} dataset ({n} top-level items) at:")
            print(f"   {args.dest}")
            print(" (sourcedata/ deliberately excluded -- it is your working tree.)")

    # Checklist
    print("-" * 70)
    print(" UPLOAD CHECKLIST (OpenNeuro):")
    print("   - Include: dataset_description.json, participants.tsv/.json, sub-*/")
    print("   - Optional: derivatives/ (FreeSurfer, defaced, scanner-derived)")
    print("   - EXCLUDE:  sourcedata/conversion/ (private working tree)")
    print("   - Confirm consent + ethics (CEP/CONEP, LGPD) before any public upload.")
    print("   - Defaced anatomicals exist under derivatives/defaced/ if you need")
    print("     to substitute them for the raw faces before sharing.")
    print("=" * 70)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
