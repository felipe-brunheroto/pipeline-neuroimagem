"""
install_schedule.py
===================

Run the batch (run_batch.py) every night on this Mac, with launchd - the
macOS scheduler, so nobody needs to keep a Terminal open.

    python code/install_schedule.py --project COGAGING --at 22:30
    python code/install_schedule.py --project COGAGING --at 22:30 --watch
    python code/install_schedule.py --project COGAGING --dry-run      # show, change nothing
    python code/install_schedule.py --project COGAGING --uninstall

WHAT IT DOES
  Writes ~/Library/LaunchAgents/org.pipeline-neuroimagem.batch.<name>.plist
  and loads it (launchctl bootstrap). At the chosen time launchd starts a shell
  that loads FreeSurfer (launchd does not read your shell profile, so the
  FREESURFER_HOME given here, or the one loaded in this Terminal, is written
  into the job) and runs
      python code/run_batch.py --project NAME --wait 720
  --wait lets a second project scheduled the same night queue behind the
  first one instead of giving up (the batch lock allows one run at a time).
  Output goes to projects/<NAME>/logs/batch/launchd.log; each run's summary to
  projects/<NAME>/logs/batch/last_summary.md.

--watch (optional): also start a run when a new folder appears in
  projects/<NAME>/Patient Files/, waiting (up to 2 h) for the copy of the CD to
  finish first. Without it, new folders are picked up by the next nightly run.

GOOD TO KNOW (only verifiable on the Mac itself)
  * A Mac that is ASLEEP at the scheduled time runs the job when it wakes up.
    To wake it for the job:  sudo pmset repeat wakeorpoweron MTWRFSU 22:25:00
    (and, for long runs, System Settings > Energy: prevent automatic sleeping
    when the display is off). While the batch runs, caffeinate keeps it awake.
  * If the project lives in Documents, Desktop, Downloads or an external
    disk, macOS privacy protection can block the nightly job ("Operation not
    permitted" in launchd.log): give /bin/bash Full Disk Access (System
    Settings > Privacy & Security), or keep the project in e.g. ~/pipeline.
  * The job runs as you, only while you are logged in (a LaunchAgent).
"""

import argparse
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import paths


def label():
    return f"org.pipeline-neuroimagem.batch.{paths.PROJECT_NAME.lower()}"


def plist_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{label()}.plist"


def job(args, fs_home):
    """The launchd job description (a dict, written with plistlib)."""
    batch = [sys.executable, str(paths.CODE_DIR / "run_batch.py"),
             "--project", paths.PROJECT_NAME, "--wait", "720"]
    if args.watch:
        batch += ["--settle-wait", "120"]
    if args.no_freesurfer:
        batch += ["--no-freesurfer"]
    script = []
    if fs_home:
        script += [f"export FREESURFER_HOME={shlex.quote(fs_home)}",
                   'if [ -f "$FREESURFER_HOME/SetUpFreeSurfer.sh" ]; then '
                   'source "$FREESURFER_HOME/SetUpFreeSurfer.sh" >/dev/null; fi']
    script += [f"cd {shlex.quote(str(paths.REPO_ROOT))}",
               "exec " + " ".join(shlex.quote(a) for a in batch)]
    log = str(paths.BATCH_DIR / "launchd.log")
    env = {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
    for name in ("PIPELINE_PROJECTS_DIR", "FS_LICENSE"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    hour, minute = (int(x) for x in args.at.split(":"))
    plist = {
        "Label": label(),
        "ProgramArguments": ["/bin/bash", "-c", "; ".join(script)],
        "EnvironmentVariables": env,
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "RunAtLoad": False,
    }
    if args.watch:
        plist["WatchPaths"] = [str(paths.PATIENT_FILES)]
        plist["ThrottleInterval"] = 900
    return plist


def launchctl(*cmd):
    """Run launchctl; (ok, text)."""
    if not shutil.which("launchctl"):
        return False, "launchctl not found (this is not macOS?)"
    r = subprocess.run(["launchctl", *cmd], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def unload():
    """Stop/unload the job if it is loaded (no error if it is not)."""
    domain = f"gui/{os.getuid()}"
    ok, _ = launchctl("bootout", f"{domain}/{label()}")
    if not ok and plist_path().is_file():
        launchctl("unload", str(plist_path()))


def check_time(text):
    try:
        hour, minute = (int(x) for x in text.split(":"))
    except ValueError:
        raise argparse.ArgumentTypeError("use HH:MM, e.g. 22:30")
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise argparse.ArgumentTypeError("use HH:MM, e.g. 22:30")
    return text


def main():
    parser = argparse.ArgumentParser(
        description="Schedule run_batch.py nightly with launchd (macOS).")
    parser.add_argument("--at", type=check_time, default="22:30",
                        help="Time of the nightly run, HH:MM (default 22:30).")
    parser.add_argument("--freesurfer-home", default=os.environ.get("FREESURFER_HOME", ""),
                        help="FreeSurfer to load for the job (default: the one "
                             "loaded in this Terminal).")
    parser.add_argument("--watch", action="store_true",
                        help="Also run when a new folder appears in Patient Files/.")
    parser.add_argument("--no-freesurfer", action="store_true",
                        help="Schedule only the steps without FreeSurfer.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the job; write and load nothing.")
    parser.add_argument("--uninstall", action="store_true",
                        help="Unload the job and delete its plist.")
    args = parser.parse_args()

    if args.uninstall:
        if args.dry_run:
            print(f"(dry run) would unload {label()} and delete {plist_path()}")
            return
        unload()
        if plist_path().is_file():
            plist_path().unlink()
            print(f"Removed {plist_path()} (the nightly batch of "
                  f"{paths.PROJECT_NAME} will not run any more).")
        else:
            print(f"Nothing to remove: {plist_path()} does not exist.")
        return

    # Every project runs recon-all, so the job must know which FreeSurfer to load.
    fs_home = "" if args.no_freesurfer else args.freesurfer_home
    if not args.no_freesurfer:
        if not fs_home or not os.path.isdir(os.path.join(fs_home, "bin")):
            sys.exit("ERROR: which FreeSurfer should the nightly job load? Load it in "
                     "this Terminal first, or pass --freesurfer-home "
                     "/Applications/freesurfer/8.2.0 (or --no-freesurfer).")

    plist = job(args, fs_home)
    data = plistlib.dumps(plist).decode()
    if args.dry_run:
        print(f"(dry run) would write {plist_path()}:\n")
        print(data)
        return

    paths.BATCH_DIR.mkdir(parents=True, exist_ok=True)
    plist_path().parent.mkdir(parents=True, exist_ok=True)
    unload()
    plist_path().write_text(data, encoding="utf-8")
    ok, text = launchctl("bootstrap", f"gui/{os.getuid()}", str(plist_path()))
    if not ok and shutil.which("launchctl"):
        ok, text = launchctl("load", "-w", str(plist_path()))
    print("=" * 70)
    print(f" Nightly batch for {paths.PROJECT_NAME}: every day at {args.at}"
          + (" + when a folder is added to Patient Files/" if args.watch else ""))
    print(f"   job file : {plist_path()}")
    print(f"   loaded   : {'yes' if ok else 'NO - ' + text}")
    print(f"   FreeSurfer: {fs_home or '(not used)'}")
    print(f"   log      : {plist['StandardOutPath']}")
    print(f"   summary  : {paths.BATCH_DIR / 'last_summary.md'}")
    print("=" * 70)
    print(" Test it now:  launchctl kickstart gui/$(id -u)/" + label())
    print(" Wake the Mac for it (once, needs your password):")
    hour, minute = (int(x) for x in args.at.split(":"))
    wake = (hour * 60 + minute - 5) % (24 * 60)
    print(f"   sudo pmset repeat wakeorpoweron MTWRFSU {wake // 60:02d}:{wake % 60:02d}:00")
    home = str(Path.home())
    if any(str(paths.REPO_ROOT).startswith(os.path.join(home, d))
           for d in ("Documents", "Desktop", "Downloads")) \
            or str(paths.REPO_ROOT).startswith("/Volumes/"):
        print(" NOTE: the project is in a folder macOS protects. If launchd.log says")
        print("   'Operation not permitted', give /bin/bash Full Disk Access (System")
        print("   Settings > Privacy & Security) or move the project to e.g. ~/pipeline.")
    if platform.system() != "Darwin":
        print(" NOTE: this is not macOS; the plist was written but launchd is not here.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
