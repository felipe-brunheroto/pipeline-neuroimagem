"""
Self-tests for install_schedule.py: the launchd job it writes, loads and
removes. launchctl is a stub that records its arguments, and HOME is a
temporary folder, so nothing is scheduled on the machine running the tests.

    python -m pytest tests/test_schedule.py -q
"""

import os
import plistlib
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic as s  # noqa: E402


def setup(tmp_path):
    projects = s.make_projects(tmp_path)
    fs = s.make_freesurfer(tmp_path)
    log = tmp_path / "launchctl.txt"
    fakebin = s.fake_bin(tmp_path, "launchctl", f'echo "$@" >> "{log}"')
    home = tmp_path / "home"
    home.mkdir()
    env = s.env_for(projects, fs, extra_path=[fakebin], HOME=home)
    plist = home / "Library/LaunchAgents/org.pipeline-neuroimagem.batch.cogaging.plist"
    return env, plist, log, projects


def test_install_watch_and_uninstall(tmp_path):
    env, plist, log, projects = setup(tmp_path)
    r = s.run("install_schedule.py", "COGAGING", env, "--at", "23:05", "--dry-run")
    assert r.returncode == 0 and "would write" in r.stdout and not plist.exists()

    r = s.run("install_schedule.py", "COGAGING", env, "--at", "23:05", "--watch")
    assert r.returncode == 0, r.stdout + r.stderr
    job = plistlib.loads(plist.read_bytes())
    assert job["Label"] == "org.pipeline-neuroimagem.batch.cogaging"
    assert job["StartCalendarInterval"] == {"Hour": 23, "Minute": 5}
    assert job["WatchPaths"] == [str(projects / "COGAGING" / "Patient Files")]
    script = job["ProgramArguments"][2]
    assert f"export FREESURFER_HOME={shlex.quote(env['FREESURFER_HOME'])}" in script
    assert "SetUpFreeSurfer.sh" in script
    assert "run_batch.py --project COGAGING --wait 720 --settle-wait 120" in script
    assert job["EnvironmentVariables"]["PIPELINE_PROJECTS_DIR"] == str(projects)
    assert job["StandardOutPath"].endswith("COGAGING/logs/batch/launchd.log")
    assert (projects / "COGAGING" / "logs" / "batch").is_dir()
    calls = log.read_text()
    assert f"bootstrap gui/{os.getuid()} {plist}" in calls
    assert "pmset repeat wakeorpoweron MTWRFSU 23:00:00" in r.stdout

    r = s.run("install_schedule.py", "COGAGING", env, "--uninstall")
    assert r.returncode == 0 and not plist.exists()
    assert "bootout gui/" in log.read_text().splitlines()[-1]


def test_install_needs_to_know_which_freesurfer(tmp_path):
    env, plist, _log, _projects = setup(tmp_path)
    env.pop("FREESURFER_HOME")
    r = s.run("install_schedule.py", "COGAGING", env)
    assert r.returncode != 0 and "which FreeSurfer" in r.stderr
    assert not plist.exists()
    r = s.run("install_schedule.py", "COGAGING", env, "--no-freesurfer", "--at", "7:30")
    assert r.returncode == 0, r.stdout + r.stderr
    job = plistlib.loads(plist.read_bytes())
    assert "--no-freesurfer" in job["ProgramArguments"][2]
    assert "FREESURFER_HOME" not in job["ProgramArguments"][2]
    r = s.run("install_schedule.py", "COGAGING", env, "--at", "25:00", "--no-freesurfer")
    assert r.returncode != 0
