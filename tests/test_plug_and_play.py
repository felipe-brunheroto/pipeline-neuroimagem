"""
Self-tests for the plug-and-play front door (code/pipeline.py, which hands each
item to code/import_inputs.py) and for the independence of the projects.
Synthetic data only, under a temporary PIPELINE_PROJECTS_DIR (inbox/ is next
to it).

    python -m pytest tests/test_plug_and_play.py -q
"""

import json
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic as s  # noqa: E402

sys.path.insert(0, str(s.CODE))
import series_classifier as sc  # noqa: E402

STAMP_IN_USE = "freesurfer-darwin-macOS-arm64-8.2.0-stub"     # synthetic.make_freesurfer
FAST = ["--min-free-gb", "0", "--no-caffeinate"]


def pipeline(ctx, *args):
    return subprocess.run([sys.executable, str(s.CODE / "pipeline.py"), *args],
                          env=ctx["env"], capture_output=True, text=True, timeout=600,
                          cwd=str(s.REPO))


def fs_run(folder, stamp=STAMP_IN_USE, done=True, tps=None):
    """A finished (or not) FreeSurfer subject folder as recon-all leaves it."""
    for sub in ("mri", "surf", "scripts", "stats"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    (folder / "mri" / "T1.mgz").write_text("stub\n")
    (folder / "scripts" / "build-stamp.txt").write_text(stamp + "\n")
    (folder / "stats" / "aseg.stats").write_text("# Measure SurfaceHoles, SurfaceHoles, x, 30, unitless\n")
    if done:
        (folder / "scripts" / "recon-all.done").write_text("done\n")
    if tps:
        (folder / "base-tps").write_text("\n".join(tps) + "\n")
    return folder


def bids_subject(folder, code):
    """A dcm2bids delivery: sub-X/ses-YYYYMM/anat/ T1w + FLAIR with sidecars."""
    anat = folder / f"sub-{code}" / "ses-202501" / "anat"
    anat.mkdir(parents=True)
    for suffix in ("T1w", "FLAIR"):
        stem = anat / f"sub-{code}_ses-202501_acq-3d_{suffix}"
        Path(str(stem) + ".nii.gz").write_bytes(b"\x1f\x8bstub")
        Path(str(stem) + ".json").write_text(json.dumps({
            "MRAcquisitionType": "3D", "InstitutionName": s.FAKE["InstitutionName"],
            "ImageType": ["ORIGINAL", "PRIMARY", "M"]}))
    return folder / f"sub-{code}"


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in Path(root).rglob("*") if p.is_file()}


@pytest.fixture
def ctx(tmp_path):
    projects = s.make_projects(tmp_path)
    fs = s.make_freesurfer(tmp_path)
    return {"root": tmp_path, "projects": projects, "inbox": tmp_path / "inbox",
            "env": s.env_for(projects, fs), "fs": fs}


# ---------------------------------------------------------------------------
# routing by study code
# ---------------------------------------------------------------------------

def test_inbox_routes_each_item_to_its_own_project(ctx):
    usb = ctx["inbox"] / "USB stick"
    s.make_exam(str(usb / "CBR-003"), s.CBR_SERIES, "20110203")
    s.make_exam(str(usb / s.exam_folder_name("GER00039", "20120304")), s.COG_SERIES, "20120304")
    s.make_exam(str(ctx["inbox"] / "exame_sem_codigo"), s.COG_SERIES, "20120304")
    bids_subject(ctx["inbox"], "GER00050")
    bids_subject(ctx["inbox"] / "cbr_bids", "CBR077")       # CBR takes DICOM only

    r = pipeline(ctx, "import", "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "would move" in r.stdout and not (ctx["projects"] / "CBR" / "Patient Files" / "CBR-003").exists()

    r = pipeline(ctx, "import")
    assert r.returncode == 0, r.stdout + r.stderr
    cbr, cog = ctx["projects"] / "CBR", ctx["projects"] / "COGAGING"
    assert (cbr / "Patient Files" / "CBR-003").is_dir()
    assert (cog / "Patient Files" / s.exam_folder_name("GER00039", "20120304")).is_dir()
    assert (cog / "sourcedata" / "received" / "sub-GER00050").is_dir()
    # Nothing of one project landed in the other.
    assert not list((cbr / "Patient Files").glob("GER*"))
    assert not list((cog / "Patient Files").glob("CBR*"))
    # Moved out of the inbox; what could not be placed is still there.
    assert not (usb / "CBR-003").exists()
    assert (ctx["inbox"] / "exame_sem_codigo").is_dir()
    assert (ctx["inbox"] / "cbr_bids" / "sub-CBR077").is_dir()
    assert "does not take delivered BIDS" in r.stdout
    assert r.stdout.count("LEFT ALONE") == 2        # the exam counted once, not per series
    # Codes only on screen (no --show-paths): never the rest of a folder name.
    for leak in s.LEAK_STRINGS:
        assert leak.lower() not in r.stdout.lower()
    # The next run finds nothing new.
    r = pipeline(ctx, "import")
    assert "moved" not in r.stdout and "copied" not in r.stdout


def test_old_pipeline_is_imported_by_copy_and_only_once(ctx, tmp_path):
    old = tmp_path / "old_copy"
    (old / "code").mkdir(parents=True)
    # older single-study layout: data folders at the top
    s.make_exam(str(old / "Patient Files" / "CBR-052"), s.CBR_SERIES, "20120901")
    fs_run(old / "bids" / "derivatives" / "freesurfer" / "CBR-011.2")
    # newer layout, a COGAGING part
    pj = old / "projects" / "COGAGING"
    s.make_exam(str(pj / "Patient Files" / s.exam_folder_name("GER00041", "20120708")),
                s.COG_SERIES, "20120708")
    bids_subject(pj / "sourcedata" / "received", "GER00060")
    (pj / "sourcedata" / "session_map.tsv").write_text(
        "subject\tsession\tstudy_date\tstudy_uid\nsub-GER00060\tses-1\t202501\timported:x\n")
    fs_run(pj / "bids" / "derivatives" / "freesurfer" / "sub-GER00041_ses-1")
    fs_run(pj / "bids" / "derivatives" / "freesurfer" / "GER00041")          # no session
    fs_run(pj / "bids" / "derivatives" / "freesurfer" / "sub-GER00041_ses-2", done=False)
    fs_run(pj / "bids" / "derivatives" / "freesurfer" / "sub-GER00070_ses-1",
           stamp="freesurfer-linux-centos7_x86_64-7.4.1-20230614-7eb8460")
    fs_run(pj / "bids" / "derivatives" / "freesurfer" / "sub-GER00080_ses-1",
           stamp="freesurfer-darwin-macOS-x86_64-8.2.0-otherbuild")
    before = snapshot(old)

    r = pipeline(ctx, "import", str(old))
    assert r.returncode == 0, r.stdout + r.stderr
    cbr, cog = ctx["projects"] / "CBR", ctx["projects"] / "COGAGING"
    assert (cbr / "Patient Files" / "CBR-052").is_dir()
    assert (cbr / "bids/derivatives/freesurfer/sub-CBR011_ses-2/scripts/recon-all.done").is_file()
    assert (cog / "Patient Files" / s.exam_folder_name("GER00041", "20120708")).is_dir()
    assert (cog / "sourcedata/received/sub-GER00060").is_dir()
    assert (cog / "sourcedata/session_map.tsv").is_file()
    fsdir = cog / "bids" / "derivatives"
    assert (fsdir / "freesurfer" / "sub-GER00041_ses-1").is_dir()
    assert (fsdir / "freesurfer-7.4.1" / "sub-GER00070_ses-1").is_dir()     # never mixed
    assert (fsdir / "freesurfer-7.4.1" / "dataset_description.json").is_file()
    for left in ("sub-GER00041_ses-2", "sub-GER00080_ses-1"):
        assert not (fsdir / "freesurfer" / left).exists()
    assert "no session in its name" in r.stdout
    assert "unfinished run" in r.stdout
    assert "another build" in r.stdout
    assert snapshot(old) == before, "a source was changed"

    r = pipeline(ctx, "import", str(old))
    assert "copied" not in r.stdout and r.stdout.count("already there") >= 6


def test_conflicts_are_reported_never_overwritten(ctx, tmp_path):
    pf = ctx["projects"] / "CBR" / "Patient Files"
    s.make_exam(str(pf / "CBR-003"), s.CBR_SERIES, "20110203")
    before = snapshot(pf)
    other = tmp_path / "other"
    s.make_exam(str(other / "CBR-003"), s.CBR_SERIES[:2], "20110203")     # same name, other content
    smap = ctx["projects"] / "COGAGING" / "sourcedata" / "session_map.tsv"
    smap.parent.mkdir(parents=True)
    smap.write_text("subject\tsession\tstudy_date\tstudy_uid\n")
    old = tmp_path / "old"
    (old / "code").mkdir(parents=True)
    (old / "projects" / "COGAGING" / "sourcedata").mkdir(parents=True)
    (old / "projects" / "COGAGING" / "sourcedata" / "session_map.tsv").write_text("different\n")
    r = pipeline(ctx, "import", str(other), str(old))
    assert r.stdout.count("CONFLICT") == 2, r.stdout
    assert "session numbers must never change" in r.stdout
    assert snapshot(pf) == before
    assert smap.read_text() == "subject\tsession\tstudy_date\tstudy_uid\n"


# ---------------------------------------------------------------------------
# run: every project, in turn; imported FreeSurfer runs are not redone
# ---------------------------------------------------------------------------

def test_run_processes_every_project_and_keeps_imported_recons(ctx):
    calls = ctx["root"] / "calls.txt"
    ctx["env"]["STUB_CALLS"] = str(calls)
    s.make_exam(str(ctx["inbox"] / "CBR-003"), s.CBR_SERIES, "20110203")
    s.make_exam(str(ctx["inbox"] / s.exam_folder_name("GER00039", "20120304")),
                s.COG_SERIES, "20120304")
    fs_run(ctx["inbox"] / "sub-GER00039_ses-1")          # its recon was done elsewhere
    bids_subject(ctx["inbox"], "GER00050")
    r = pipeline(ctx, "run", *FAST)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    for name in ("CBR", "COGAGING"):
        summary = (ctx["projects"] / name / "logs/batch/last_summary.md").read_text()
        assert "result: **OK**" in summary
    cog = ctx["projects"] / "COGAGING" / "bids"
    assert (cog / "sub-GER00039" / "ses-1" / "anat").is_dir()
    assert (cog / "sub-GER00050").is_dir()                       # import_bids ran
    assert (cog / "derivatives/freesurfer/sub-GER00050_ses-1/scripts/recon-all.done").is_file()
    recon = [c for c in calls.read_text().splitlines() if c.startswith("recon-all")]
    assert not any("-s sub-GER00039_ses-1 " in c for c in recon), "imported recon redone"
    assert any("sub-CBR003_ses-1" in c for c in recon)
    r = pipeline(ctx, "run", *FAST)
    assert r.returncode == 0 and "ran: nothing" in r.stdout
    r = pipeline(ctx, "status")
    assert "CBR " in r.stdout and "COGAGING " in r.stdout


def test_one_nightly_job_for_all_projects(ctx, tmp_path):
    home = tmp_path / "home"
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "org.pipeline-neuroimagem.batch.cbr.plist").write_text("old")
    log = tmp_path / "launchctl.txt"
    fakebin = s.fake_bin(tmp_path, "launchctl", f'echo "$@" >> "{log}"')
    ctx["env"].update(HOME=str(home), PATH=f"{fakebin}:{ctx['env']['PATH']}")
    r = pipeline(ctx, "schedule", "--at", "22:30", "--watch")
    assert r.returncode == 0, r.stdout + r.stderr
    job = plistlib.loads((agents / "org.pipeline-neuroimagem.nightly.plist").read_bytes())
    assert "pipeline.py run --wait 720 --settle-wait 120" in job["ProgramArguments"][2]
    assert str(ctx["inbox"]) in job["WatchPaths"]
    assert not (agents / "org.pipeline-neuroimagem.batch.cbr.plist").exists()


def test_start_command_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(s.REPO / "start.command")], capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------------------
# independence: no project inherits another's rules or protocol facts
# ---------------------------------------------------------------------------

def test_no_study_rules_in_the_shared_map():
    assert sc.load_series_map() == [], "study rules belong in projects/<NAME>/series_map.csv"
    assert sc.load_series_map(str(s.REPO / "projects" / "CBR" / "series_map.csv"))


def test_cogaging_does_not_get_cbr_protocol_facts(ctx):
    pf = ctx["projects"] / "COGAGING" / "Patient Files"
    series = s.COG_SERIES + [("pCASL 3D ASL", False, 2), ("gre_field_mapping", False, 2)]
    s.make_exam(str(pf / s.exam_folder_name("GER00039", "20120304")), series, "20120304")
    for script in ("dicom_to_bids.py", "deidentify_sidecars.py", "make_derivatives.py"):
        r = s.run(script, "COGAGING", ctx["env"])
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    sd = ctx["projects"] / "COGAGING" / "bids" / "sourcedata"
    asl = json.loads(next((sd / "asl").rglob("*_asl.json")).read_text())
    assert asl["ArterialSpinLabelingType"] == "REQUIRED_FILL_ME"
    assert asl["M0Type"] == "REQUIRED_FILL_ME"
    readme = (sd / "asl" / "README").read_text()
    assert "multi-inversion-time" not in readme and "Absent" not in readme
    assert "PHASE ONLY" not in (sd / "fmap" / "README").read_text()
    assert not list((sd / "fmap").rglob("*_phase.json"))
