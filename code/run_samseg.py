"""
run_samseg.py
=============

White-matter lesion (hyperintensity) volume with FreeSurfer SAMSEG, from the
T1w + FLAIR of each session. Replicates the Cog-Aging guide, section 4, as one
command that loops over every session, skips what is done, and writes one
table:

  1. mri_coreg + mri_vol2vol : FLAIR registered to the T1      (guide 4.2)
  2. run_samseg --lesion      : T1 + registered FLAIR, lesion pattern 0 1
                                (T1 not hyperintense, FLAIR hyperintense),
                                --threshold 0.3, --save-posteriors (guide 4.3)
  3. mri_binarize --match 99  : lesion_mask.mgz for the visual QC (guide 4.5)
  4. samseg_lesions.tsv       : lesion mm3, intracranial mm3 and lesion % of
                                intracranial for every session (guide 4.4)

Runs on the Mac with FreeSurfer (~5 min per session). Independent of recon-all.

OUTPUT (bids/derivatives/samseg/):
  sub-X_ses-Y/            one SAMSEG run (seg.mgz, samseg.stats, posteriors/,
                          FLAIR_reg.nii.gz, flair2t1.lta, lesion_mask.mgz,
                          run_info.json, samseg_pipeline.log)
  samseg_lesions.tsv      one row per session

THRESHOLD: the lesion volume in samseg.stats comes from the threshold SAMSEG
ran with. To test another threshold, run again with --threshold X: sessions
finished with a different threshold are re-run, so the mask on screen and the
volume in the table always match (binarizing the posteriors by hand at another
threshold would change the mask but not the number).

MACHINE: --lesion uses TensorFlow, which needs AVX. An Intel FreeSurfer running
under Rosetta on Apple Silicon has no AVX and aborts ("Abort trap: 6"); this
script stops early in that case. Use a native arm64 FreeSurfer (the Mac Mini).

HOW TO RUN
----------
    python code/run_samseg.py --project COGAGING --dry-run
    python code/run_samseg.py --project COGAGING
    python code/run_samseg.py --project COGAGING --threshold 0.5
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import paths
import run_recon_long as rrl

LESION_LABEL = "99"
TABLE_FIELDS = ["subject", "session", "lesion_mm3", "intracranial_mm3",
                "lesion_pct_icv", "threshold", "freesurfer_version"]


def samseg_measures(stats_path):
    """samseg.stats -> {measure: value} ('# Measure Lesions, 3355.2, mm^3')."""
    out = {}
    try:
        with open(stats_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("# Measure"):
                    parts = [p.strip() for p in line[len("# Measure"):].split(",")]
                    if len(parts) >= 2:
                        try:
                            out[parts[0]] = float(parts[1])
                        except ValueError:
                            pass
    except OSError:
        pass
    return out


def run_info(out_dir):
    try:
        with open(os.path.join(out_dir, "run_info.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def is_done(out_dir, threshold):
    """Finished with this threshold: stats + mask present and run_info agrees."""
    return (os.path.isfile(os.path.join(out_dir, "samseg.stats"))
            and os.path.isfile(os.path.join(out_dir, "lesion_mask.mgz"))
            and run_info(out_dir).get("threshold") == threshold)


def commands(t1, flair, out_dir, threshold, threads):
    reg = os.path.join(out_dir, "flair2t1.lta")
    flair_reg = os.path.join(out_dir, "FLAIR_reg.nii.gz")
    return [
        ["mri_coreg", "--mov", flair, "--ref", t1, "--reg", reg],
        ["mri_vol2vol", "--mov", flair, "--targ", t1, "--reg", reg, "--o", flair_reg],
        ["run_samseg", "-o", out_dir, "-i", t1, "-i", flair_reg, "--lesion",
         "--lesion-mask-pattern", "0", "1", "--threshold", str(threshold),
         "--save-posteriors", "--threads", str(threads)],
        ["mri_binarize", "--i", os.path.join(out_dir, "seg.mgz"),
         "--match", LESION_LABEL, "--o", os.path.join(out_dir, "lesion_mask.mgz")],
    ]


def rosetta_freesurfer(fs_home):
    """True when an Intel (x86_64-only) FreeSurfer is used on Apple Silicon."""
    if platform.machine() != "arm64":
        return False
    binary = os.path.join(fs_home or "", "bin", "mri_coreg")
    try:
        kind = subprocess.run(["file", "-b", binary], capture_output=True,
                              text=True).stdout
    except Exception:
        return False
    return "x86_64" in kind and "arm64" not in kind


def write_dataset_description(samseg_dir, fs_version):
    path = os.path.join(samseg_dir, "dataset_description.json")
    if os.path.isfile(path):
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "Name": f"{paths.PROJECT['title']} SAMSEG white-matter lesions",
            "BIDSVersion": "1.9.0",
            "DatasetType": "derivative",
            "GeneratedBy": [{
                "Name": "FreeSurfer SAMSEG (run_samseg --lesion)",
                "Version": fs_version or "unknown",
                "Description": "T1w + FLAIR registered to T1 (mri_coreg), lesion "
                               "mask pattern 0 1; see run_info.json per session.",
            }],
            "SourceDatasets": [{"Description": "Raw anatomicals in this BIDS dataset."}],
        }, f, indent="\t", ensure_ascii=False)
        f.write("\n")
    with open(os.path.join(samseg_dir, ".bidsignore"), "w", encoding="utf-8") as f:
        f.write("sub-*/\n")


def process(item, threshold, threads, fs_version, env):
    sub, ses, t1, flair, out_dir = item
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, "samseg_pipeline.log")
    with open(log_path, "w", encoding="utf-8") as log:
        for cmd in commands(t1, flair, out_dir, threshold, threads):
            log.write("$ " + " ".join(cmd) + "\n")
            log.flush()
            rc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                                env=env, cwd=out_dir).returncode
            if rc != 0:
                return sub, ses, rc, log_path
    with open(os.path.join(out_dir, "run_info.json"), "w", encoding="utf-8") as f:
        json.dump({
            "threshold": threshold,
            "lesion_mask_pattern": [0, 1],
            "freesurfer_version": fs_version,
            "t1w": os.path.relpath(t1, paths.BIDS_ROOT),
            "flair": os.path.relpath(flair, paths.BIDS_ROOT),
            "finished": datetime.now().isoformat(timespec="seconds"),
        }, f, indent="\t")
        f.write("\n")
    return sub, ses, 0, log_path


def write_table(samseg_dir):
    """samseg_lesions.tsv from every finished session folder."""
    rows = []
    for name in sorted(os.listdir(samseg_dir)):
        out_dir = os.path.join(samseg_dir, name)
        stats = samseg_measures(os.path.join(out_dir, "samseg.stats"))
        if "_ses-" not in name or "Lesions" not in stats:
            continue
        sub, ses = name.split("_", 1)
        icv = stats.get("Intra-Cranial")
        info = run_info(out_dir)
        rows.append({
            "subject": sub, "session": ses,
            "lesion_mm3": f"{stats['Lesions']:.1f}",
            "intracranial_mm3": f"{icv:.1f}" if icv else "n/a",
            "lesion_pct_icv": f"{100.0 * stats['Lesions'] / icv:.3f}" if icv else "n/a",
            "threshold": info.get("threshold", "n/a"),
            "freesurfer_version": info.get("freesurfer_version", "n/a"),
        })
    path = os.path.join(samseg_dir, "samseg_lesions.tsv")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\t".join(TABLE_FIELDS) + "\n")
        for r in rows:
            f.write("\t".join(str(r[k]) for k in TABLE_FIELDS) + "\n")
    return path, rows


def main():
    parser = argparse.ArgumentParser(
        description="White-matter lesions with FreeSurfer SAMSEG (T1w + FLAIR).")
    parser.add_argument("--input", default=str(paths.BIDS_ROOT),
                        help="BIDS root with sub-*/ses-*/anat (default: the project's bids/).")
    parser.add_argument("--threshold", type=float, default=0.3,
                        help="Lesion posterior threshold (default 0.3; higher = "
                             "fewer false positives).")
    parser.add_argument("--threads", type=int, default=8,
                        help="Threads per SAMSEG run (default 8).")
    parser.add_argument("--jobs", type=int, default=1,
                        help="Sessions to run in parallel (default 1).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan; run nothing (no FreeSurfer needed).")
    parser.add_argument("--force", action="store_true",
                        help="Re-run sessions that are already finished.")
    args = parser.parse_args()

    tree = rrl.discover(args.input)
    samseg_dir = str(paths.SAMSEG_DIR)
    todo, done, missing = [], [], []
    for sub in sorted(tree):
        for ses in sorted(tree[sub]):
            anat = tree[sub][ses]
            if not anat.get("T1w") or not anat.get("FLAIR"):
                missing.append(f"{sub}/{ses}")
                continue
            out_dir = os.path.join(samseg_dir, f"{sub}_{ses}")
            item = (sub, ses, anat["T1w"], anat["FLAIR"], out_dir)
            (done if is_done(out_dir, args.threshold) and not args.force else todo).append(item)

    print("=" * 70)
    print(f" SAMSEG white-matter lesions  (project {paths.PROJECT_NAME})")
    print(f" input     : {args.input}")
    print(f" output    : {samseg_dir}")
    print(f" threshold : {args.threshold}   threads/run: {args.threads}   "
          f"parallel jobs: {args.jobs}")
    print("=" * 70)
    for sub, ses, *_ in done:
        print(f"  [done] {sub}/{ses}")
    for m in missing:
        print(f"  [skip] {m}: needs both T1w and FLAIR")
    for sub, ses, t1, flair, out_dir in todo:
        print(f"\n  {sub}/{ses}")
        for cmd in commands(t1, flair, out_dir, args.threshold, args.threads):
            print("      " + " ".join(cmd))

    if args.dry_run:
        print("\n(dry run - nothing executed)")
        return
    failures = 0
    if not todo:
        print("\n Nothing to run.")
    else:
        missing_tools = [t for t in ("mri_coreg", "mri_vol2vol", "run_samseg",
                                     "mri_binarize") if not shutil.which(t)]
        if missing_tools:
            print(f"\nERROR: {', '.join(missing_tools)} not found. Load FreeSurfer "
                  "first (on the Mac Mini: type  freesurfer  in this Terminal).")
            sys.exit(1)
        fs_home = os.environ.get("FREESURFER_HOME", "")
        if rosetta_freesurfer(fs_home):
            print("\nERROR: this FreeSurfer is an Intel build running under Rosetta. "
                  "SAMSEG --lesion needs AVX and aborts here (Abort trap: 6). Run "
                  "this step on the Mac Mini (native arm64 FreeSurfer).")
            sys.exit(1)
        fs_version = rrl.fs_build_stamp(fs_home)
        os.makedirs(samseg_dir, exist_ok=True)
        write_dataset_description(samseg_dir, fs_version)
        print(f"\n FreeSurfer version: {fs_version or '(unknown)'}\n")
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(process, item, args.threshold, args.threads,
                                   fs_version, dict(os.environ)) for item in todo]
            for fut in as_completed(futures):
                sub, ses, rc, log_path = fut.result()
                status = "OK" if rc == 0 else f"FAILED(rc={rc})"
                print(f"  [{status}] {sub}/{ses}  (log: {log_path})")
                failures += rc != 0
        if failures:
            print(f"\n {failures} session(s) failed; read their logs.")

    if os.path.isdir(samseg_dir):
        table, rows = write_table(samseg_dir)
        print(f"\n Table: {table}  ({len(rows)} session(s))")
        for r in rows:
            print(f"   {r['subject']}/{r['session']}: lesion {r['lesion_mm3']} mm3 "
                  f"= {r['lesion_pct_icv']}% of intracranial")
    print("\n QC (essential): open each session and check the red mask sits on the")
    print(" white FLAIR spots (deep/periventricular), not on ventricle edges or cortex:")
    print("   cd <output>/sub-X_ses-Y && freeview FLAIR_reg.nii.gz "
          "lesion_mask.mgz:colormap=heat:opacity=0.4")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
