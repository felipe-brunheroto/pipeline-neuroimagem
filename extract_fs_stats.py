"""
extract_fs_stats.py
===================

The final pipeline step: turn FreeSurfer recon output into tidy tables of brain
measures (the "volumes" the students extract). It wraps FreeSurfer's own
`asegstats2table` (subcortical volumes) and `aparcstats2table` (cortical
thickness / area / volume per region), so we are NOT reinventing anything.

Runs on the recon Mac, after run_recon_long.py has produced FreeSurfer subjects.
Use --dry-run anywhere to see the plan without FreeSurfer installed.

OUTPUT (into <output-dir>, default BIDS_output/derivatives/fs_tables/):
  aseg_volumes.tsv          subcortical + global volumes per subject
  lh.aparc.thickness.tsv    left cortical thickness per region
  rh.aparc.thickness.tsv    right cortical thickness per region
  lh.aparc.area.tsv / rh... cortical surface area per region
  lh.aparc.volume.tsv / rh  cortical grey-matter volume per region

Each table has one row per FreeSurfer subject and one column per region - ready
to open in Excel or load for statistics.

USAGE
-----
    python extract_fs_stats.py                       # uses $SUBJECTS_DIR
    python extract_fs_stats.py --subjects-dir ~/fs_subjects
    python extract_fs_stats.py --dry-run             # preview, no FreeSurfer needed
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path


def find_subjects(subjects_dir):
    """
    Return the list of FreeSurfer subjects that finished recon-all, i.e. have a
    stats/aseg.stats file. Skips fsaverage, logs, and unfinished runs. If
    longitudinal '.long.' subjects exist, those are the ones to analyse, but we
    return every completed subject and let the user see the full list.
    """
    subjects = []
    root = Path(subjects_dir)
    if not root.is_dir():
        return subjects
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in ("fsaverage", "logs") or entry.name.startswith("."):
            continue
        if (entry / "stats" / "aseg.stats").is_file():
            subjects.append(entry.name)
    return subjects


def build_commands(subjects, output_dir, measures):
    """Build the asegstats2table / aparcstats2table command list."""
    cmds = []
    # Subcortical + global volumes (one call).
    cmds.append((
        "aseg_volumes.tsv",
        ["asegstats2table", "--subjects", *subjects, "--meas", "volume",
         "--tablefile", os.path.join(output_dir, "aseg_volumes.tsv")],
    ))
    # Cortical measures, per hemisphere and measure.
    for hemi in ("lh", "rh"):
        for meas in measures:
            fname = f"{hemi}.aparc.{meas}.tsv"
            cmds.append((
                fname,
                ["aparcstats2table", "--subjects", *subjects, "--hemi", hemi,
                 "--meas", meas, "--tablefile", os.path.join(output_dir, fname)],
            ))
    return cmds


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Extract tidy FreeSurfer volume/thickness tables.")
    parser.add_argument("--subjects-dir",
                        default=os.environ.get("SUBJECTS_DIR",
                                               str(here / "freesurfer_subjects")),
                        help="FreeSurfer SUBJECTS_DIR (default: $SUBJECTS_DIR or "
                             "./freesurfer_subjects).")
    parser.add_argument("--output-dir",
                        default=str(here / "BIDS_output" / "derivatives" /
                                    "fs_tables"),
                        help="Where to write the tables.")
    parser.add_argument("--measures", default="thickness,area,volume",
                        help="Cortical measures to extract (comma list).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan; run nothing (no FreeSurfer needed).")
    args = parser.parse_args()

    measures = [m.strip() for m in args.measures.split(",") if m.strip()]
    subjects = find_subjects(args.subjects_dir)

    print("=" * 70)
    print(" Extract FreeSurfer tables (volumes / thickness / area)")
    print(f" SUBJECTS_DIR : {args.subjects_dir}")
    print(f" output       : {args.output_dir}")
    print("=" * 70)

    if not subjects:
        print("No completed FreeSurfer subjects found (need stats/aseg.stats).")
        print("Run run_recon_long.py first, or check --subjects-dir.")
        sys.exit(0 if args.dry_run else 1)

    print(f" {len(subjects)} subject(s): " + ", ".join(subjects))
    cmds = build_commands(subjects, args.output_dir, measures)

    print("\nPlanned tables:")
    for fname, cmd in cmds:
        print(f"  {fname}\n      {' '.join(cmd)}")

    if args.dry_run:
        print("\n(dry run - nothing executed)")
        return

    import shutil
    if not shutil.which("asegstats2table"):
        print("\nERROR: FreeSurfer tools not found. Source FreeSurfer 8 first:")
        print("  source $FREESURFER_HOME/SetUpFreeSurfer.sh")
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    env = dict(os.environ)
    env["SUBJECTS_DIR"] = args.subjects_dir

    failures = 0
    for fname, cmd in cmds:
        print(f"\n-> {fname}")
        result = subprocess.run(cmd, env=env)
        if result.returncode != 0:
            failures += 1
            print(f"   [warn] {fname} failed (see message above).")

    print("\n" + "=" * 70)
    print(f" DONE. {len(cmds) - failures}/{len(cmds)} tables written to:")
    print(f"   {args.output_dir}")
    print("=" * 70)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
