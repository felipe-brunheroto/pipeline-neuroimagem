"""
fs_stub.py
==========

Stand-in for the FreeSurfer commands the pipeline calls, for the self-tests
(see synthetic.make_freesurfer, which puts one wrapper per tool on PATH). Each
tool writes the files the pipeline checks for, in the same places FreeSurfer
does, and nothing else. It mimics the behaviours that matter for re-runs:

  * recon-all refuses '-i' on a subject folder that already exists (as the real
    one does after a failed run), and leaves scripts/IsRunning.* while running;
  * base/long refuse to start when the timepoints they need are not done.

Test controls (environment):
  STUB_FAIL   comma list of ids that fail (recon target id, SAMSEG session
              folder name, or defaced file name)
  STUB_SLEEP  seconds each recon-all sleeps (for the lock / crash tests)
  STUB_CALLS  file to which every call is appended (one line per call)
"""

import os
import shutil
import sys
import time


def arg(args, flag, default=None):
    return args[args.index(flag) + 1] if flag in args else default


def touch(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def failing(item):
    return item in [s for s in os.environ.get("STUB_FAIL", "").split(",") if s]


def recon_all(args):
    sd = os.environ["SUBJECTS_DIR"]
    if "-base" in args:
        target = arg(args, "-base")
        tps = [args[i + 1] for i, a in enumerate(args) if a == "-tp"]
        needs = tps
    elif "-long" in args:
        tp, base = args[args.index("-long") + 1:args.index("-long") + 3]
        target, needs = f"{tp}.long.{base}", [tp, base]
    else:
        target, needs = arg(args, "-s"), []
    for need in needs:
        if not os.path.isfile(os.path.join(sd, need, "scripts", "recon-all.done")):
            print(f"ERROR: {need} has not finished recon-all")
            return 1
    out = os.path.join(sd, target)
    if "-i" in args and os.path.isdir(out):
        print("ERROR: You are trying to re-run an existing subject with "
              "(possibly) new input data (-i).")
        return 1
    scripts = os.path.join(out, "scripts")
    running = os.path.join(scripts, "IsRunning.lh+rh")
    touch(running, f"SUBJECT {target}\nPROCESSID {os.getpid()}\n")
    touch(os.path.join(scripts, "recon-all.log"), " ".join(args) + "\n")
    stamp = os.path.join(os.environ.get("FREESURFER_HOME", ""), "build-stamp.txt")
    if os.path.isfile(stamp):
        shutil.copy(stamp, os.path.join(scripts, "build-stamp.txt"))
    time.sleep(float(os.environ.get("STUB_SLEEP", "0")))
    if failing(target):
        touch(os.path.join(scripts, "recon-all.error"), "stub failure\n")
        os.remove(running)
        print(f"ERROR: stub failure injected in mri_ca_register for {target}")
        return 1
    for name in ("mri/T1.mgz", "mri/brainmask.mgz", "mri/wm.mgz", "mri/aseg.mgz",
                 "surf/lh.white", "surf/lh.pial", "surf/rh.white", "surf/rh.pial"):
        touch(os.path.join(out, name), "stub\n")
    touch(os.path.join(out, "stats", "aseg.stats"),
          "# Measure SurfaceHoles, SurfaceHoles, Total number of defect holes "
          "in surfaces prior to fixing, 23, unitless\n"
          "# Measure EstimatedTotalIntraCranialVol, eTIV, Estimated Total "
          "Intracranial Volume, 1500000.0, mm^3\n")
    touch(os.path.join(scripts, "recon-all.done"), "stub\n")
    os.remove(running)
    print(f"recon-all {target} finished without error")
    return 0


def run_samseg(args):
    out = arg(args, "-o")
    if failing(os.path.basename(out)):
        print("ERROR: stub failure injected in SAMSEG")
        return 1
    touch(os.path.join(out, "seg.mgz"), "stub\n")
    touch(os.path.join(out, "posteriors", "Lesions.mgz"), "stub\n")
    touch(os.path.join(out, "samseg.stats"),
          "# Measure Lesions, 1234.5, mm^3\n# Measure Intra-Cranial, 1500000.0, mm^3\n")
    return 0


def table(args):
    subjects = args[args.index("--subjects") + 1:]
    subjects = subjects[:next((i for i, a in enumerate(subjects) if a.startswith("--")),
                              len(subjects))]
    touch(arg(args, "--tablefile"), "Measure\n" + "\n".join(subjects) + "\n")
    return 0


def mideface(args):
    dest = arg(args, "--o")
    if failing(os.path.basename(dest)):
        print("ERROR: stub failure injected in mideface")
        return 1
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy(arg(args, "--i"), dest)
    return 0


def output_file(args, flag):
    touch(arg(args, flag), "stub\n")
    return 0


TOOLS = {
    "recon-all": recon_all,
    "run_samseg": run_samseg,
    "asegstats2table": table,
    "aparcstats2table": table,
    "mideface": mideface,
    "mri_coreg": lambda a: output_file(a, "--reg"),
    "mri_vol2vol": lambda a: output_file(a, "--o"),
    "mri_binarize": lambda a: output_file(a, "--o"),
    "freeview": lambda a: 0,
}


def main():
    tool, args = sys.argv[1], sys.argv[2:]
    calls = os.environ.get("STUB_CALLS")
    if calls:
        with open(calls, "a") as f:
            f.write(tool + " " + " ".join(args) + "\n")
    sys.exit(TOOLS[tool](args))


if __name__ == "__main__":
    main()
