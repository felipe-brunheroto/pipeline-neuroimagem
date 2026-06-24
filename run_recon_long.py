"""
run_recon_long.py
=================

PURPOSE
-------
Drive the **FreeSurfer longitudinal stream** (Reuter et al., 2012) over the
project's anatomical scans, for within-subject change across timepoints plus
cross-sectional measures for between-subject / clinical correlation.

This is the recon step. It is meant to run on the dedicated FreeSurfer machine
(the M4 / 64 GB Mac with **FreeSurfer 8**, native Apple-Silicon - no Rosetta).
It does NOT run on the conversion Mac (no FreeSurfer there). Use --dry-run
anywhere to print the exact plan without FreeSurfer installed.

THE THREE STAGES
----------------
  1. cross : `recon-all -all` on each subject/timepoint independently.
  2. base  : an unbiased within-subject template per subject that has >1 visit
             (`recon-all -base ... -tp ... -tp ...`).
  3. long  : `recon-all -long <tp> <base> -all` for each timepoint vs its base.
Single-visit subjects (e.g. baseline-only) get the cross-sectional run only;
they slot into the longitudinal stream automatically once later visits arrive.

INPUT
-----
An anatomical root (default `bids/`). For each `*/anat/` folder it picks, by
keyword, the T1 (required) and optionally a FLAIR or T2 to refine the pial
surface (`-FLAIRpial` / `-T2pial`). Keyword discovery works on BOTH the BIDS
names (`..._T1w.nii.gz`) and the defaced/descriptive names (`..._t1_mprage...`),
so you can point --input at the DEFACED tree for a privacy-preserving recon:
    python run_recon_long.py --input BIDS_output/derivatives/defaced

EXAMPLES
--------
    python run_recon_long.py --dry-run                 # print the full plan
    python run_recon_long.py --subjects-dir ~/fs_subjects --jobs 4 --threads 4
    python run_recon_long.py --stages cross            # only the cross stage
"""

import os
import re
import sys
import argparse
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


# Keyword rules to pick the anatomical role from a file name (FLAIR first, as a
# FLAIR here is a T2-SPACE variant whose name also contains "t2"/"space").
def anat_role(filename):
    text = filename.lower()
    if any(k in text for k in ["flair", "fl3", "da-fl", "dark"]):
        return "FLAIR"
    if any(k in text for k in ["t1", "mprage", "mp2rage", "spgr", "bravo", "tfl"]):
        return "T1w"
    if any(k in text for k in ["t2", "space", "tse"]):
        return "T2w"
    return None


def discover(input_root):
    """
    Return {subject: {session: {"T1w": path, "FLAIR": path?, "T2w": path?}}}
    by scanning <input_root>/sub-*/ses-*/anat/*.nii.gz.
    """
    tree = {}
    root = Path(input_root)
    for anat_nifti in sorted(root.glob("sub-*/ses-*/anat/*.nii.gz")):
        parts = anat_nifti.parts
        sub = next((p for p in parts if p.startswith("sub-")), None)
        ses = next((p for p in parts if p.startswith("ses-")), None)
        if not sub or not ses:
            continue
        role = anat_role(anat_nifti.name)
        if role is None:
            continue
        slot = tree.setdefault(sub, {}).setdefault(ses, {})
        # First match wins per role (deterministic via sorted()).
        slot.setdefault(role, str(anat_nifti))
    return tree


def fs_id(sub, ses):
    """FreeSurfer cross-sectional subject id for a timepoint, e.g.
    'sub-CBR003_ses-2' (kept filesystem-safe and unambiguous)."""
    return f"{sub}_{ses}"


def recon_done(subjects_dir, fs_subject):
    """True if a recon-all run for this FS subject already completed."""
    return os.path.isfile(os.path.join(subjects_dir, fs_subject,
                                       "scripts", "recon-all.done"))


def build_plan(tree, subjects_dir, pial, threads):
    """
    Build the staged command plan. Returns a dict with lists of
    (label, fs_subject, command) tuples for 'cross', 'base', 'long'.
    """
    plan = {"cross": [], "base": [], "long": []}

    for sub in sorted(tree):
        sessions = sorted(tree[sub])

        # ---- cross-sectional, one per timepoint ----------------------------
        for ses in sessions:
            anat = tree[sub][ses]
            t1 = anat.get("T1w")
            if not t1:
                print(f"  [warn] no T1 for {sub}/{ses}; skipping this timepoint.")
                continue
            sid = fs_id(sub, ses)
            cmd = ["recon-all", "-s", sid, "-i", t1]
            if pial == "flair" and anat.get("FLAIR"):
                cmd += ["-FLAIR", anat["FLAIR"], "-FLAIRpial"]
            elif pial == "t2" and anat.get("T2w"):
                cmd += ["-T2", anat["T2w"], "-T2pial"]
            cmd += ["-all", "-openmp", str(threads)]
            plan["cross"].append(("cross", sid, cmd))

        # ---- base + long only for subjects with >=2 timepoints -------------
        tps = [fs_id(sub, ses) for ses in sessions if tree[sub][ses].get("T1w")]
        if len(tps) >= 2:
            base_cmd = ["recon-all", "-base", sub]
            for tp in tps:
                base_cmd += ["-tp", tp]
            base_cmd += ["-all", "-openmp", str(threads)]
            plan["base"].append(("base", sub, base_cmd))
            for tp in tps:
                long_id = f"{tp}.long.{sub}"
                long_cmd = ["recon-all", "-long", tp, sub, "-all",
                            "-openmp", str(threads)]
                plan["long"].append(("long", long_id, long_cmd))

    return plan


def run_stage(stage_name, items, subjects_dir, jobs, force, env):
    """Run one stage's commands with up to `jobs` in parallel."""
    to_run = []
    for label, fs_subject, cmd in items:
        if not force and recon_done(subjects_dir, fs_subject):
            print(f"  [skip] {fs_subject} already done.")
            continue
        to_run.append((fs_subject, cmd))
    if not to_run:
        print(f"  ({stage_name}: nothing to do)")
        return 0

    log_dir = os.path.join(subjects_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    def _one(fs_subject, cmd):
        log_path = os.path.join(log_dir, f"{fs_subject}.{stage_name}.log")
        with open(log_path, "w", encoding="utf-8") as logf:
            logf.write(" ".join(cmd) + "\n\n")
            logf.flush()
            proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT,
                                  env=env)
        return fs_subject, proc.returncode, log_path

    failures = 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(_one, s, c) for s, c in to_run]
        for fut in as_completed(futures):
            fs_subject, rc, log_path = fut.result()
            status = "OK" if rc == 0 else f"FAILED(rc={rc})"
            print(f"  [{status}] {fs_subject}  (log: {log_path})")
            if rc != 0:
                failures += 1
    return failures


def write_fs_dataset_description(subjects_dir):
    """Declare the FreeSurfer SUBJECTS_DIR as a BIDS derivatives dataset, and
    .bidsignore the native FreeSurfer files (which are not BIDS-conforming)."""
    import json
    desc = os.path.join(subjects_dir, "dataset_description.json")
    if not os.path.isfile(desc):
        with open(desc, "w", encoding="utf-8") as f:
            json.dump({
                "Name": "CBR / NEUROCOVID FreeSurfer recon",
                "BIDSVersion": "1.9.0",
                "DatasetType": "derivative",
                "GeneratedBy": [{
                    "Name": "freesurfer",
                    "Version": os.environ.get("FREESURFER_HOME", "8"),
                    "Description": "recon-all longitudinal stream "
                                   "(cross / base / long).",
                }],
                "SourceDatasets": [{"Description": "Raw (or defaced) anatomicals "
                                                   "in this BIDS dataset."}],
            }, f, indent="\t", ensure_ascii=False)
            f.write("\n")
    bidsignore = os.path.join(subjects_dir, ".bidsignore")
    if not os.path.isfile(bidsignore):
        with open(bidsignore, "w", encoding="utf-8") as f:
            # FreeSurfer subject folders are not BIDS-named; exclude from the
            # validator while keeping them inside the derivatives dataset.
            f.write("sub-*/\nfsaverage/\nlogs/\n")


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="FreeSurfer longitudinal recon driver (FreeSurfer 8).")
    parser.add_argument("--input", default=str(here / "bids"),
                        help="Anatomical root (default: ./bids).")
    parser.add_argument("--subjects-dir",
                        default=os.environ.get(
                            "SUBJECTS_DIR",
                            str(here / "bids" / "derivatives" / "freesurfer")),
                        help="FreeSurfer SUBJECTS_DIR (default: $SUBJECTS_DIR or "
                             "./bids/derivatives/freesurfer, a BIDS derivative).")
    parser.add_argument("--pial", choices=["flair", "t2", "none"], default="flair",
                        help="Refine pial surface with FLAIR or T2 (default: flair).")
    parser.add_argument("--threads", type=int, default=4,
                        help="OpenMP threads PER recon-all (default: 4).")
    parser.add_argument("--jobs", type=int, default=1,
                        help="recon-all processes to run in PARALLEL (default: 1). "
                             "Budget ~8 GB RAM and `threads` cores per job.")
    parser.add_argument("--stages", default="cross,base,long",
                        help="Comma list of stages to run (default: all).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan; run nothing (no FreeSurfer needed).")
    parser.add_argument("--force", action="store_true",
                        help="Re-run even if recon-all.done exists.")
    args = parser.parse_args()

    if not os.path.isdir(args.input):
        print(f"ERROR: input root not found: {args.input}")
        sys.exit(1)

    tree = discover(args.input)
    if not tree:
        print(f"No sub-*/ses-*/anat anatomicals found under {args.input}.")
        sys.exit(1)

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    plan = build_plan(tree, args.subjects_dir, args.pial, args.threads)

    print("=" * 70)
    print(" FreeSurfer longitudinal recon plan")
    print(f" input        : {args.input}")
    print(f" SUBJECTS_DIR : {args.subjects_dir}")
    print(f" pial refine  : {args.pial}   threads/run: {args.threads}   "
          f"parallel jobs: {args.jobs}")
    print("=" * 70)
    for sub in sorted(tree):
        sess = ", ".join(sorted(tree[sub]))
        print(f"  {sub}: {sess}")
    print("-" * 70)
    for stage in stages:
        print(f"\n[{stage}]  ({len(plan[stage])} run(s))")
        for _label, fs_subject, cmd in plan[stage]:
            print(f"  {fs_subject}\n      {' '.join(cmd)}")

    if args.dry_run:
        print("\n(dry run - nothing executed)")
        return

    # ---- real run: FreeSurfer must be available ----------------------------
    import shutil
    if not shutil.which("recon-all"):
        print("\nERROR: 'recon-all' not found. Source FreeSurfer 8 first:")
        print("  export FREESURFER_HOME=/Applications/freesurfer/8.0.0")
        print("  source $FREESURFER_HOME/SetUpFreeSurfer.sh")
        print("Then re-run. (Or use --dry-run to preview the plan.)")
        sys.exit(1)

    env = dict(os.environ)
    env["SUBJECTS_DIR"] = args.subjects_dir
    os.makedirs(args.subjects_dir, exist_ok=True)
    write_fs_dataset_description(args.subjects_dir)

    fs_home = env.get("FREESURFER_HOME", "(unset)")
    print(f"\n using FREESURFER_HOME={fs_home}")
    print(f" using recon-all: {shutil.which('recon-all')}\n")

    total_failures = 0
    # Stages MUST run in dependency order: all cross before base, base before long.
    for stage in ["cross", "base", "long"]:
        if stage not in stages:
            continue
        print(f"\n===== STAGE: {stage} =====")
        total_failures += run_stage(stage, plan[stage], args.subjects_dir,
                                    args.jobs, args.force, env)

    print("\n" + "=" * 70)
    print(f" DONE. failures={total_failures}. Outputs in {args.subjects_dir}")
    print(" Next: QC the surfaces (freeview / Euler number), then extract metrics")
    print("       with asegstats2table / aparcstats2table for stats.")
    print("=" * 70)
    sys.exit(1 if total_failures else 0)


if __name__ == "__main__":
    main()
