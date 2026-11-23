"""
Self-tests for qc_intensity.py on SYNTHETIC FreeSurfer folders only: tiny .mgz
volumes written with nibabel and stub mri_segstats / mri_synthseg on PATH, all
under a temporary PIPELINE_PROJECTS_DIR. The real FreeSurfer is never needed.

    python -m pytest tests/test_qc_intensity.py -q
"""

import csv
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic as s  # noqa: E402

sys.path.insert(0, str(s.CODE))
import batch_lock  # noqa: E402

# Stub mri_segstats: a wmparc table whose WM means depend on the image, so the
# expected summaries are known: norm.mgz -> poles 100, centre 110, rest 110;
# nu.mgz -> poles 90, centre 100, rest 100. Every region has 100 voxels.
SEGSTATS_STUB = r'''
import sys
args = sys.argv[1:]
image = args[args.index("--i") + 1]
out = args[args.index("--sum") + 1]
poles = {32, 33, 12, 14, 9}
nu = image.endswith("nu.mgz")
lines = ["# Title Segmentation Statistics",
         "# ColHeaders  Index SegId NVoxels Volume_mm3 StructName Mean StdDev Min Max Range"]
i = 0
for offset, hemi in ((3000, "lh"), (4000, "rh")):
    for k in range(1, 36):
        i += 1
        mean = (90.0 if nu else 100.0) if k in poles else (100.0 if nu else 110.0)
        lines.append(f"{i:3d} {offset + k:5d} 100 100.0 wm-{hemi}-r{k} {mean:.4f} 5.0000 0 200 200")
for label in (5001, 5002):
    i += 1
    lines.append(f"{i:3d} {label:5d} 100 100.0 WM-unsegmented {(100.0 if nu else 110.0):.4f} 5.0 0 200 200")
lines.append(f"{i + 1:3d}    17 50 50.0 Left-Hippocampus 80.0 5.0 0 200 200")
open(out, "w").write("\n".join(lines) + "\n")
'''

# Stub mri_synthseg: volumes 10% above the aseg values written below.
SYNTHSEG_STUB = r'''
import os, sys
args = sys.argv[1:]
vol = args[args.index("--vol") + 1]
seg = args[args.index("--o") + 1]
open(seg, "w").write("stub\n")
open(vol, "w").write("subject,left hippocampus,right hippocampus,left amygdala,"
                     "right amygdala,left lateral ventricle,right lateral ventricle\n"
                     "x,4400,4400,1650,1650,11000,11000\n")
'''

ASEG = """# Measure BrainSeg, BrainSegVol, Brain Segmentation Volume, 1100000.0, mm^3
# Measure lhSurfaceHoles, lhSurfaceHoles, Number of defect holes in lh surfaces prior to fixing, 20, unitless
# Measure rhSurfaceHoles, rhSurfaceHoles, Number of defect holes in rh surfaces prior to fixing, 17, unitless
# Measure SurfaceHoles, SurfaceHoles, Total number of defect holes in surfaces prior to fixing, 37, unitless
# ColHeaders  Index SegId NVoxels Volume_mm3 StructName normMean normStdDev normMin normMax normRange
  1   4  10000 10000.0  Left-Lateral-Ventricle 30.0 5.0 0 100 100
  2  17   4000  4000.0  Left-Hippocampus 80.0 5.0 0 100 100
  3  18   1500  1500.0  Left-Amygdala 80.0 5.0 0 100 100
  4  43  10000 10000.0  Right-Lateral-Ventricle 30.0 5.0 0 100 100
  5  53   4000  4000.0  Right-Hippocampus 80.0 5.0 0 100 100
  6  54   1500  1500.0  Right-Amygdala 80.0 5.0 0 100 100
"""


def pct_stats(poles_value, other_value):
    """A ?h.w-g.pct.stats with every DK region (1000 vertices each)."""
    names = ["bankssts", "frontalpole", "temporalpole", "lateralorbitofrontal",
             "medialorbitofrontal", "inferiortemporal", "precentral", "postcentral",
             "superiorparietal", "superiorfrontal"]
    poles = {"frontalpole", "temporalpole", "lateralorbitofrontal",
             "medialorbitofrontal", "inferiortemporal"}
    lines = ["# ColHeaders Index SegId NVertices Area_mm2 StructName Mean StdDev Min Max Range SNR"]
    for i, name in enumerate(names, start=1):
        value = poles_value if name in poles else other_value
        lines.append(f"{i} {i} 1000 700.0 {name} {value} 4.0 0 40 40 5.0")
    return "\n".join(lines) + "\n"


def write_session(folder, field=1.2, rh_pct=True, done=True):
    """A finished FreeSurfer session: mri volumes (nu = orig x field inside
    the brain), stats files and recon-all.done."""
    nib = pytest.importorskip("nibabel")
    import numpy as np
    mri = folder / "mri"
    mri.mkdir(parents=True)
    affine = np.eye(4)
    shape = (8, 8, 8)
    orig = np.full(shape, 50.0, dtype=np.float32)
    mask = np.zeros(shape, dtype=np.float32)
    mask[2:6, 2:6, 2:6] = 1
    nu = orig.copy()
    nu[mask > 0] *= field
    brain = orig * mask
    for name, data in (("orig", orig), ("nu", nu), ("brainmask", brain),
                       ("norm", brain), ("wmparc", mask * 3032),
                       ("rawavg", orig)):
        nib.save(nib.MGHImage(data, affine), str(mri / f"{name}.mgz"))
    stats = folder / "stats"
    stats.mkdir()
    (stats / "aseg.stats").write_text(ASEG)
    (stats / "lh.w-g.pct.stats").write_text(pct_stats(18.0, 22.0))
    if rh_pct:
        (stats / "rh.w-g.pct.stats").write_text(pct_stats(18.0, 22.0))
    scripts = folder / "scripts"
    scripts.mkdir()
    (scripts / "build-stamp.txt").write_text("freesurfer-darwin-macOS-arm64-8.2.0-stub\n")
    if done:
        (scripts / "recon-all.done").write_text("stub\n")


@pytest.fixture
def ctx(tmp_path):
    projects = s.make_projects(tmp_path)
    fs_dir = projects / "CBR" / "bids" / "derivatives" / "freesurfer"
    write_session(fs_dir / "sub-CBR901_ses-1")
    write_session(fs_dir / "sub-CBR901_ses-2", field=1.5)
    write_session(fs_dir / "sub-CBR901_ses-1.long.sub-CBR901", rh_pct=False)
    write_session(fs_dir / "sub-CBR901")                      # base: skipped
    write_session(fs_dir / "sub-CBR902_ses-1", done=False)    # unfinished: skipped
    for tool, code in (("mri_segstats", SEGSTATS_STUB), ("mri_synthseg", SYNTHSEG_STUB)):
        script = tmp_path / f"{tool}_stub.py"
        script.write_text(code)
        s.fake_bin(tmp_path, tool, f'exec "{sys.executable}" "{script}" "$@"')
    env = s.env_for(projects)
    # Only the stubs: on a machine with FreeSurfer loaded, the real tools must
    # never be reached from a test (e.g. after a stub is removed).
    env["PATH"] = os.pathsep.join([str(tmp_path / "fakebin"), "/usr/bin", "/bin"])
    return {"root": tmp_path, "projects": projects, "fs": fs_dir, "env": env,
            "out": projects / "CBR" / "bids" / "derivatives" / "qc-intensidade"}


def tree_hash(root):
    """Content + name + mtime of every file under root (read-only check)."""
    h = hashlib.sha256()
    for path in sorted(Path(root).rglob("*")):
        h.update(str(path.relative_to(root)).encode())
        if path.is_file():
            h.update(path.read_bytes())
            h.update(str(path.stat().st_mtime_ns).encode())
    return h.hexdigest()


def read_tsv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def qc(ctx, *args):
    return s.run("qc_intensity.py", "CBR", ctx["env"], *args)


# ---------------------------------------------------------------------------
# end to end
# ---------------------------------------------------------------------------

def test_dry_run_plans_and_writes_nothing(ctx):
    before = tree_hash(ctx["projects"])
    proc = qc(ctx, "--dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert tree_hash(ctx["projects"]) == before
    assert not ctx["out"].exists()
    out = proc.stdout
    assert "[cross] sub-CBR901_ses-1" in out
    assert "[cross] sub-CBR901_ses-2" in out
    assert "[long] sub-CBR901_ses-1.long.sub-CBR901" in out
    assert "sub-CBR902_ses-1" not in out          # not finished
    assert "2 cross, 1 long" in out and "base templates skipped: 1" in out
    assert "mri_segstats --seg" in out and "mri_synthseg --i" in out
    assert "gw_contrast_rh" in out                # missing in the long session
    assert "DRY RUN" in out


def test_full_run_measures_and_leaves_freesurfer_untouched(ctx):
    pytest.importorskip("nibabel")
    before = tree_hash(ctx["fs"])
    proc = qc(ctx)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert tree_hash(ctx["fs"]) == before, "the FreeSurfer folder was modified"

    rows = {r["fs_subject"]: r for r in read_tsv(ctx["out"] / "sessions.tsv")}
    assert set(rows) == {"sub-CBR901_ses-1", "sub-CBR901_ses-2",
                         "sub-CBR901_ses-1.long.sub-CBR901"}
    r = rows["sub-CBR901_ses-1"]
    assert r["stream"] == "cross"
    assert float(r["norm_wm_poles"]) == pytest.approx(100.0)
    assert float(r["norm_wm_centre"]) == pytest.approx(110.0)
    assert float(r["norm_wm_poles_minus_centre"]) == pytest.approx(-10.0)
    assert r["norm_wm_n_outside_110pm5"] == "10"   # 5 pole regions x 2 hemis
    assert "lh-frontalpole" in r["norm_wm_outside_110pm5"]
    assert "precentral" not in r["norm_wm_outside_110pm5"]
    assert float(r["nu_wm_poles_over_centre"]) == pytest.approx(0.9)
    assert float(r["n4_field_p2"]) == pytest.approx(1.2)
    assert float(r["n4_field_p98_over_p2"]) == pytest.approx(1.0)
    assert float(rows["sub-CBR901_ses-2"]["n4_field_p50"]) == pytest.approx(1.5)
    assert float(r["gw_contrast_poles_minus_centre"]) == pytest.approx(-4.0)
    assert (r["surface_holes"], r["surface_holes_lh"], r["surface_holes_rh"]) == \
        ("37", "20", "17")
    assert r["fs_build_stamp"].endswith("8.2.0-stub")
    assert r["not_measured"] == ""

    long_row = rows["sub-CBR901_ses-1.long.sub-CBR901"]
    assert "gw_contrast_rh" in long_row["not_measured"]

    ss = read_tsv(ctx["out"] / "synthseg_vs_aseg.tsv")
    # 6 structures for each cross session, and the long session reuses ses-1's
    assert len(ss) == 18
    assert {r["diff_pct"] for r in ss} == {"10.00"}

    wm = read_tsv(ctx["out"] / "wm_intensity.tsv")
    assert {r["image"] for r in wm} == {"norm", "nu"}
    assert not any(r["region"] == "Left-Hippocampus" for r in wm)

    desc = json.loads((ctx["out"] / "dataset_description.json").read_text())
    assert desc["DatasetType"] == "derivative"
    assert (ctx["out"] / "segstats" / "sub-CBR901_ses-1.norm.wmparc.sum").is_file()
    assert (ctx["out"] / "qc_intensity_log.txt").is_file()


def test_without_synthseg_it_says_so(ctx):
    pytest.importorskip("nibabel")
    (ctx["root"] / "fakebin" / "mri_synthseg").unlink()
    proc = qc(ctx)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rows = read_tsv(ctx["out"] / "sessions.tsv")
    cross = [r for r in rows if r["stream"] == "cross"]
    assert all("mri_synthseg not found" in r["not_measured"] for r in cross)
    assert read_tsv(ctx["out"] / "synthseg_vs_aseg.tsv") == []


def test_refuses_while_the_batch_holds_the_lock(ctx):
    lock = batch_lock.acquire(str(ctx["projects"] / ".batch.lock"),
                              {"project": "CBR", "pid": 1, "started": "tonight"})
    assert lock is not None
    try:
        proc = qc(ctx)
    finally:
        lock.close()
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "A BATCH IS RUNNING" in proc.stdout
    assert not ctx["out"].exists()


def test_output_inside_freesurfer_folder_is_refused(ctx):
    before = tree_hash(ctx["fs"])
    proc = qc(ctx, "--out", str(ctx["fs"] / "qc"), "--dry-run")
    assert proc.returncode != 0
    assert "must be outside SUBJECTS_DIR" in proc.stdout + proc.stderr
    assert tree_hash(ctx["fs"]) == before


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def qi(tmp_path, monkeypatch):
    """qc_intensity imported against a synthetic projects/ folder, without
    leaving its `paths` behind for other tests."""
    projects = s.make_projects(tmp_path)
    monkeypatch.setenv("PIPELINE_PROJECTS_DIR", str(projects))
    monkeypatch.setenv("PIPELINE_PROJECT", "CBR")
    saved = {k: sys.modules.pop(k) for k in ("paths", "run_recon_long", "qc_intensity")
             if k in sys.modules}
    try:
        yield importlib.import_module("qc_intensity")
    finally:
        for k in ("paths", "run_recon_long", "qc_intensity"):
            sys.modules.pop(k, None)
        sys.modules.update(saved)


def test_wm_regions_map_wmparc_labels(qi):
    rows = [{"SegId": 3032.0, "NVoxels": 10.0, "Mean": 100.0, "StdDev": 1.0},
            {"SegId": 4024.0, "NVoxels": 30.0, "Mean": 112.0, "StdDev": 1.0},
            {"SegId": 5001.0, "NVoxels": 5.0, "Mean": 110.0, "StdDev": 1.0},
            {"SegId": 1032.0, "NVoxels": 5.0, "Mean": 70.0, "StdDev": 1.0},   # cortex
            {"SegId": 3033.0, "NVoxels": 0.0, "Mean": 0.0, "StdDev": 0.0}]    # empty
    wm = qi.wm_regions(rows)
    assert [(r["hemi"], r["region"]) for r in wm] == [
        ("lh", "frontalpole"), ("rh", "precentral"), ("lh", "unsegmentedwm")]
    assert qi.pooled_mean(wm, ["frontalpole", "precentral"]) == pytest.approx(109.0)
    assert qi.outside_target(wm) == ["lh-frontalpole"]


def test_measures_and_names(qi, tmp_path):
    path = tmp_path / "aseg.stats"
    path.write_text(ASEG)
    m = qi.parse_measures(str(path))
    assert m["SurfaceHoles"] == 37 and m["rhSurfaceHoles"] == 17
    assert qi.norm_name("Left-Lateral-Ventricle") == qi.norm_name("left lateral ventricle")
    table = qi.parse_stats_table(str(path))
    assert table[1]["StructName"] == "Left-Hippocampus"
    assert table[1]["Volume_mm3"] == 4000.0
