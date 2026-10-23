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
    python code/run_recon_long.py --input bids/derivatives/defaced

EXAMPLES
--------
    python run_recon_long.py --dry-run                 # print the full plan
    python run_recon_long.py --subjects-dir ~/fs_subjects --jobs 4 --threads 4
    python run_recon_long.py --stages cross            # only the cross stage
"""

import os
import re
import json
import sys
import argparse
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import paths


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


def bids_role(filename):
    """The BIDS suffix (T1w/T2w/FLAIR) of an anat file, or the keyword guess for
    non-BIDS names. Contrast-enhanced scans (ce-) are never used for recon."""
    if "_ce-" in filename:
        return None
    m = re.search(r"_(T1w|T2w|FLAIR)\.nii(\.gz)?$", filename)
    return m.group(1) if m else anat_role(filename)


def _is_3d(nifti_path):
    """True if the sidecar says 3D (MRAcquisitionType); False if 2D or unknown."""
    sidecar = re.sub(r"\.nii(\.gz)?$", ".json", str(nifti_path))
    try:
        with open(sidecar, encoding="utf-8") as f:
            return json.load(f).get("MRAcquisitionType") == "3D"
    except Exception:
        return False


def discover(input_root):
    """
    Return {subject: {session: {"T1w": path, "FLAIR": path?, "T2w": path?}}}
    by scanning <input_root>/sub-*/ses-*/anat/*.nii.gz.

    When a session has more than one candidate for a role (clinical CDs often
    carry a 3D T1 plus 2D T1s), the 3D acquisition wins, then the first name in
    sorted order; the choice is printed so it can be checked.
    """
    candidates = {}
    root = Path(input_root)
    for anat_nifti in sorted(root.glob("sub-*/ses-*/anat/*.nii.gz")):
        parts = anat_nifti.parts
        sub = next((p for p in parts if p.startswith("sub-")), None)
        ses = next((p for p in parts if p.startswith("ses-")), None)
        if not sub or not ses:
            continue
        role = bids_role(anat_nifti.name)
        if role is None:
            continue
        candidates.setdefault((sub, ses, role), []).append(anat_nifti)

    tree = {}
    for (sub, ses, role), paths_found in sorted(candidates.items()):
        chosen = sorted(paths_found, key=lambda p: (not _is_3d(p), p.name))[0]
        if len(paths_found) > 1:
            print(f"  [note] {sub}/{ses}: {len(paths_found)} {role} candidates; "
                  f"using {chosen.name}")
        tree.setdefault(sub, {}).setdefault(ses, {})[role] = str(chosen)
    return tree


def fs_id(sub, ses):
    """FreeSurfer cross-sectional subject id for a timepoint, e.g.
    'sub-CBR003_ses-2' (kept filesystem-safe and unambiguous)."""
    return f"{sub}_{ses}"


def recon_done(subjects_dir, fs_subject):
    """True if a recon-all run for this FS subject already completed."""
    return os.path.isfile(os.path.join(subjects_dir, fs_subject,
                                       "scripts", "recon-all.done"))


def build_plan(tree, subjects_dir, pial, threads, extra_flags=()):
    """
    Build the staged command plan. Returns a dict with lists of
    (label, fs_subject, command) tuples for 'cross', 'base', 'long'.
    extra_flags (project.json "recon.flags", e.g. -qcache -3T) are added to
    the cross-sectional runs; base/long inherit their processing from them.
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
            cmd += ["-all"] + list(extra_flags) + ["-openmp", str(threads)]
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


def fs_build_stamp(fs_home):
    """FreeSurfer's own version string ($FREESURFER_HOME/build-stamp.txt)."""
    try:
        with open(os.path.join(fs_home or "", "build-stamp.txt"), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def stamps_in(subjects_dir):
    """{build-stamp: [fs_subject, ...]} for the recons already in SUBJECTS_DIR
    (recon-all copies the stamp into each subject's scripts/ folder)."""
    found = {}
    for stamp_file in Path(subjects_dir).glob("*/scripts/build-stamp.txt"):
        stamp = stamp_file.read_text(encoding="utf-8", errors="replace").strip()
        found.setdefault(stamp, []).append(stamp_file.parent.parent.name)
    return found


def write_fs_dataset_description(subjects_dir):
    """Declare the FreeSurfer SUBJECTS_DIR as a BIDS derivatives dataset, and
    .bidsignore the native FreeSurfer files (which are not BIDS-conforming)."""
    import json
    desc = os.path.join(subjects_dir, "dataset_description.json")
    if not os.path.isfile(desc):
        with open(desc, "w", encoding="utf-8") as f:
            json.dump({
                "Name": f"{paths.PROJECT['title']} FreeSurfer recon",
                "BIDSVersion": "1.9.0",
                "DatasetType": "derivative",
                "GeneratedBy": [{
                    "Name": "freesurfer",
                    "Version": fs_build_stamp(os.environ.get("FREESURFER_HOME"))
                               or os.environ.get("FREESURFER_HOME", "unknown"),
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
    parser = argparse.ArgumentParser(
        description="FreeSurfer longitudinal recon driver (FreeSurfer 8).")
    parser.add_argument("--input", default=str(paths.BIDS_ROOT),
                        help="Anatomical root (default: the project's bids/ root; "
                             "pass bids/derivatives/defaced for a private recon).")
    parser.add_argument("--subjects-dir",
                        default=os.environ.get("SUBJECTS_DIR",
                                               str(paths.FREESURFER_DIR)),
                        help="FreeSurfer SUBJECTS_DIR (default: $SUBJECTS_DIR or "
                             "bids/derivatives/freesurfer, a BIDS derivative).")
    recon_cfg = paths.PROJECT.get("recon", {})
    parser.add_argument("--pial", choices=["flair", "t2", "none"],
                        default=recon_cfg.get("pial", "flair"),
                        help="Refine pial surface with FLAIR or T2 (default: from "
                             "project.json, else flair).")
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
    parser.add_argument("--allow-mixed-versions", action="store_true",
                        help="Run even if SUBJECTS_DIR already holds recons from "
                             "another FreeSurfer version (not recommended).")
    parser.add_argument("--exclude", default="",
                        help="Comma list of FreeSurfer ids to leave out of this run "
                             "(e.g. sub-X_ses-1,sub-X); used by run_batch.py for "
                             "runs that keep failing.")
    args = parser.parse_args()
    extra_flags = recon_cfg.get("flags", [])

    if not os.path.isdir(args.input):
        print(f"ERROR: input root not found: {args.input}")
        sys.exit(1)

    tree = discover(args.input)
    if not tree:
        print(f"No sub-*/ses-*/anat anatomicals found under {args.input}.")
        sys.exit(1)

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    plan = build_plan(tree, args.subjects_dir, args.pial, args.threads, extra_flags)
    excluded = {s.strip() for s in args.exclude.split(",") if s.strip()}
    if excluded:
        for stage in plan:
            plan[stage] = [item for item in plan[stage] if item[1] not in excluded]

    print("=" * 70)
    print(" FreeSurfer longitudinal recon plan")
    print(f" input        : {args.input}")
    print(f" SUBJECTS_DIR : {args.subjects_dir}")
    print(f" pial refine  : {args.pial}   threads/run: {args.threads}   "
          f"parallel jobs: {args.jobs}")
    print(f" extra flags  : {' '.join(extra_flags) or '(none)'}   "
          f"(project {paths.PROJECT_NAME})")
    if excluded:
        print(f" left out     : {', '.join(sorted(excluded))}")
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
    current = fs_build_stamp(fs_home)
    print(f"\n using FREESURFER_HOME={fs_home}")
    print(f" FreeSurfer version: {current or '(unknown: no build-stamp.txt)'}")
    print(f" using recon-all: {shutil.which('recon-all')}\n")

    # One cohort, one FreeSurfer version: volumes and thickness differ between
    # versions, so mixing them inside one SUBJECTS_DIR is a confound.
    existing = stamps_in(args.subjects_dir)
    other = {stamp: subs for stamp, subs in existing.items() if stamp != current}
    if other:
        print(" WARNING: SUBJECTS_DIR already holds recons from another "
              "FreeSurfer version:")
        for stamp, subs in other.items():
            print(f"   {stamp}: {len(subs)} subject(s), e.g. {', '.join(sorted(subs)[:3])}")
        if not args.allow_mixed_versions:
            print(" Stopping. Use the same FreeSurfer for the whole cohort, or pass "
                  "--allow-mixed-versions if this is intended.")
            sys.exit(1)

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
