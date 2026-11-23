"""
qc_intensity.py
===============

Intensity-inhomogeneity (B1 bias field) QC on FreeSurfer runs that ALREADY
exist. It never runs recon-all and never writes inside the FreeSurfer folder:
every subject folder is opened read-only, and every output goes to
``bids/derivatives/qc-intensidade/`` (a BIDS derivative of its own).

For each finished cross-sectional and longitudinal session (the -base template
is skipped) it measures:

  (a) white-matter intensity per Desikan-Killiany region, from
      ``mri_segstats --seg mri/wmparc.mgz`` on ``mri/norm.mgz`` (FreeSurfer
      target 110) AND on ``mri/nu.mgz`` (after N4, before FreeSurfer's own
      normalisation, where a residual bias is still visible). Summary: pooled
      poles (frontal pole, temporal pole, lateral and medial orbitofrontal,
      inferior temporal) versus centre (precentral, postcentral, superior
      parietal), and the regions whose norm.mgz mean is outside 110 +/- 5;
  (b) the amplitude of the field N4 removed: percentiles 2, 50 and 98 of
      nu.mgz / orig.mgz inside brainmask.mgz, and p98/p2 (scale-free, since
      nu.mgz is also globally rescaled);
  (c) grey/white contrast per DK region, read from the stats/?h.w-g.pct.stats
      that recon-all's pctsurfcon step already wrote (not re-run here, as it
      would write inside the subject folder);
  (d) SurfaceHoles (total, lh, rh) from stats/aseg.stats;
  (e) if mri_synthseg runs, SynthSeg volumes (on the session's mri/rawavg.mgz)
      versus aseg for hippocampus, amygdala and lateral ventricles.

Anything that cannot be measured is listed per session in ``not_measured``,
never silently dropped.

Outputs (in --out, default bids/derivatives/qc-intensidade/):
    dataset_description.json  .bidsignore
    sessions.tsv              one summary row per FreeSurfer session
    wm_intensity.tsv          (a) every WM region, norm and nu
    gw_contrast.tsv           (c) every cortical region
    synthseg_vs_aseg.tsv      (e)
    segstats/  synthseg/      raw tool outputs
    qc_intensity_log.txt      commands run and FreeSurfer versions

    python code/qc_intensity.py --project CBR --dry-run   # plan only, writes nothing
    python code/qc_intensity.py --project CBR

It respects the machine-wide batch lock (projects/.batch.lock): a real run
refuses to start while the nightly batch holds it, and holds it itself while
running, so the batch cannot start on top of it.
"""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import paths
import batch_lock
import run_recon_long as rrl

# Desikan-Killiany index -> name (the order of FreeSurfer's aparc colour table;
# wmparc labels are 3000 + index on the left and 4000 + index on the right).
DK = {
    1: "bankssts", 2: "caudalanteriorcingulate", 3: "caudalmiddlefrontal",
    4: "corpuscallosum", 5: "cuneus", 6: "entorhinal", 7: "fusiform",
    8: "inferiorparietal", 9: "inferiortemporal", 10: "isthmuscingulate",
    11: "lateraloccipital", 12: "lateralorbitofrontal", 13: "lingual",
    14: "medialorbitofrontal", 15: "middletemporal", 16: "parahippocampal",
    17: "paracentral", 18: "parsopercularis", 19: "parsorbitalis",
    20: "parstriangularis", 21: "pericalcarine", 22: "postcentral",
    23: "posteriorcingulate", 24: "precentral", 25: "precuneus",
    26: "rostralanteriorcingulate", 27: "rostralmiddlefrontal",
    28: "superiorfrontal", 29: "superiorparietal", 30: "superiortemporal",
    31: "supramarginal", 32: "frontalpole", 33: "temporalpole",
    34: "transversetemporal", 35: "insula",
}
WM_OFFSET = {"lh": 3000, "rh": 4000}
UNSEGMENTED_WM = {5001: "lh", 5002: "rh"}

POLES = ["frontalpole", "temporalpole", "lateralorbitofrontal",
         "medialorbitofrontal", "inferiortemporal"]
CENTRE = ["precentral", "postcentral", "superiorparietal"]
NORM_TARGET, NORM_TOLERANCE = 110.0, 5.0

# aseg structure names compared with SynthSeg.
SYNTHSEG_STRUCTURES = ["Left-Hippocampus", "Right-Hippocampus",
                       "Left-Amygdala", "Right-Amygdala",
                       "Left-Lateral-Ventricle", "Right-Lateral-Ventricle"]

CROSS = re.compile(r"^(sub-[A-Za-z0-9]+)_(ses-[A-Za-z0-9]+)$")
LONG = re.compile(r"^(sub-[A-Za-z0-9]+)_(ses-[A-Za-z0-9]+)\.long\.(sub-[A-Za-z0-9]+)$")
BASE = re.compile(r"^sub-[A-Za-z0-9]+$")


# ---------------------------------------------------------------------------
# reading FreeSurfer outputs (pure functions, no tool calls)
# ---------------------------------------------------------------------------

def find_sessions(subjects_dir, only_subjects=()):
    """Finished FreeSurfer sessions in SUBJECTS_DIR, as dicts with fs_subject,
    subject, session, stream ('cross' / 'long') and folder. The -base templates
    and anything not named by run_recon_long.py are skipped."""
    sessions = []
    root = Path(subjects_dir)
    if not root.is_dir():
        return sessions
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        name = folder.name
        m_long, m_cross = LONG.match(name), CROSS.match(name)
        if m_long:
            sub, ses, stream = m_long.group(1), m_long.group(2), "long"
        elif m_cross:
            sub, ses, stream = m_cross.group(1), m_cross.group(2), "cross"
        else:
            continue
        if only_subjects and sub not in only_subjects:
            continue
        if not rrl.recon_done(subjects_dir, name):
            continue
        sessions.append({"fs_subject": name, "subject": sub, "session": ses,
                         "stream": stream, "folder": str(folder)})
    return sessions


def base_templates(subjects_dir):
    root = Path(subjects_dir)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and BASE.match(p.name))


def parse_stats_table(path):
    """Rows of a FreeSurfer stats table (mri_segstats --sum, aseg.stats,
    ?h.w-g.pct.stats): column names from the '# ColHeaders' line; numbers
    converted to float where possible."""
    columns, rows = None, []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("# ColHeaders"):
                columns = line.split()[2:]
                continue
            if line.startswith("#") or not line.strip() or columns is None:
                continue
            values = line.split()
            row = {}
            for key, value in zip(columns, values):
                try:
                    row[key] = float(value)
                except ValueError:
                    row[key] = value
            rows.append(row)
    return rows


def parse_measures(path):
    """{'SurfaceHoles': 23.0, 'lhSurfaceHoles': ..., ...} from the
    '# Measure' lines of a stats file (the second field is the key)."""
    measures = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("# Measure"):
                continue
            parts = [p.strip() for p in line[len("# Measure"):].split(",")]
            if len(parts) >= 4:
                try:
                    measures[parts[1]] = float(parts[3])
                except ValueError:
                    pass
    return measures


def wm_regions(segstats_rows):
    """WM rows of a wmparc segstats table -> list of
    {hemi, region, label, nvox, mean, sd} (cortical-WM regions and the
    unsegmented WM)."""
    out = []
    for row in segstats_rows:
        label = int(row.get("SegId", -1))
        nvox = row.get("NVoxels", 0)
        if not nvox:
            continue
        hemi = region = None
        for h, offset in WM_OFFSET.items():
            if label - offset in DK:
                hemi, region = h, DK[label - offset]
        if label in UNSEGMENTED_WM:
            hemi, region = UNSEGMENTED_WM[label], "unsegmentedwm"
        if region is None:
            continue
        out.append({"hemi": hemi, "region": region, "label": label,
                    "nvox": int(nvox), "mean": row.get("Mean"),
                    "sd": row.get("StdDev")})
    return out


def pooled_mean(rows, regions, weight="nvox"):
    """Volume- (or area-) weighted mean of `mean` over the rows whose region is
    in `regions` (both hemispheres). None when nothing matches."""
    chosen = [r for r in rows if r["region"] in regions
              and isinstance(r.get("mean"), float) and r.get(weight)]
    total = sum(r[weight] for r in chosen)
    if not total:
        return None
    return sum(r["mean"] * r[weight] for r in chosen) / total


def outside_target(rows, regions=POLES + CENTRE,
                   target=NORM_TARGET, tolerance=NORM_TOLERANCE):
    """'hemi-region' of the rows (of `regions`) whose mean is outside
    target +/- tolerance."""
    return [f"{r['hemi']}-{r['region']}" for r in rows
            if r["region"] in regions and isinstance(r.get("mean"), float)
            and abs(r["mean"] - target) > tolerance]


def gw_contrast(stats_path, hemi):
    """Rows of ?h.w-g.pct.stats -> [{hemi, region, nvert, area, mean, sd}]."""
    out = []
    for row in parse_stats_table(stats_path):
        region = row.get("StructName")
        if region not in DK.values():
            continue
        out.append({"hemi": hemi, "region": region,
                    "nvert": int(row.get("NVertices", 0) or 0),
                    "area": row.get("Area_mm2"), "mean": row.get("Mean"),
                    "sd": row.get("StdDev")})
    return out


def field_percentiles(nu_path, orig_path, mask_path):
    """Percentiles 2/50/98 of nu/orig inside the brain mask (and p98/p2).
    Needs nibabel + numpy (FreeSurfer's fspython has both)."""
    import nibabel as nib
    import numpy as np
    nu = np.asanyarray(nib.load(nu_path).dataobj, dtype=np.float32)
    orig = np.asanyarray(nib.load(orig_path).dataobj, dtype=np.float32)
    mask = np.asanyarray(nib.load(mask_path).dataobj)
    if not (nu.shape == orig.shape == mask.shape):
        raise ValueError(f"grids differ: nu {nu.shape}, orig {orig.shape}, "
                         f"brainmask {mask.shape}")
    inside = (mask > 0) & (orig > 0)
    if not inside.any():
        raise ValueError("empty brain mask")
    ratio = nu[inside] / orig[inside]
    p2, p50, p98 = (float(v) for v in np.percentile(ratio, [2, 50, 98]))
    return {"p2": p2, "p50": p50, "p98": p98,
            "p98_over_p2": p98 / p2 if p2 else None}


def synthseg_volumes(csv_path):
    """{normalised structure name: mm3} from mri_synthseg --vol."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return {}
    header, values = rows[0], rows[1]
    out = {}
    for key, value in zip(header, values):
        try:
            out[norm_name(key)] = float(value)
        except ValueError:
            pass
    return out


def norm_name(name):
    """'Left-Lateral-Ventricle' and 'left lateral ventricle' -> same key."""
    return re.sub(r"[\s_\-]+", " ", name.strip().lower())


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------

def segstats_cmd(folder, image, sum_file):
    return ["mri_segstats", "--seg", os.path.join(folder, "mri", "wmparc.mgz"),
            "--i", os.path.join(folder, "mri", f"{image}.mgz"),
            "--ctab-default", "--excludeid", "0", "--sum", sum_file]


def synthseg_cmd(rawavg, out_dir, threads):
    return ["mri_synthseg", "--i", rawavg,
            "--o", os.path.join(out_dir, "synthseg.mgz"),
            "--vol", os.path.join(out_dir, "volumes.csv"),
            "--threads", str(threads)]


def build_plan(sessions, out_dir, synthseg=True, threads=4):
    """Per session: the tool commands to run, the files read, and what cannot
    be measured (missing inputs). Pure: looks at the files, runs nothing."""
    plan = []
    synthseg_done = set()
    for s in sessions:
        f = s["folder"]
        has = lambda rel: os.path.isfile(os.path.join(f, rel))  # noqa: E731
        item = dict(s, commands=[], reads=[], missing=[])
        for image in ("norm", "nu"):
            if has("mri/wmparc.mgz") and has(f"mri/{image}.mgz"):
                item["commands"].append((f"wm_{image}", segstats_cmd(
                    f, image, os.path.join(out_dir, "segstats",
                                           f"{s['fs_subject']}.{image}.wmparc.sum"))))
            else:
                item["missing"].append(f"wm_{image}: needs mri/wmparc.mgz and "
                                       f"mri/{image}.mgz")
        if all(has(f"mri/{n}.mgz") for n in ("nu", "orig", "brainmask")):
            item["reads"].append("n4_field: mri/nu.mgz / mri/orig.mgz in mri/brainmask.mgz")
        else:
            item["missing"].append("n4_field: needs mri/nu.mgz, orig.mgz, brainmask.mgz")
        for hemi in ("lh", "rh"):
            if has(f"stats/{hemi}.w-g.pct.stats"):
                item["reads"].append(f"gw_contrast: stats/{hemi}.w-g.pct.stats")
            else:
                item["missing"].append(f"gw_contrast_{hemi}: stats/{hemi}.w-g.pct.stats "
                                       "absent (pctsurfcon not run by recon-all)")
        if has("stats/aseg.stats"):
            item["reads"].append("surface_holes + aseg volumes: stats/aseg.stats")
        else:
            item["missing"].append("surface_holes: stats/aseg.stats absent")
        key = (s["subject"], s["session"])
        if synthseg:
            if s["stream"] == "cross" and has("mri/rawavg.mgz") and key not in synthseg_done:
                synthseg_done.add(key)
                item["commands"].append(("synthseg", synthseg_cmd(
                    os.path.join(f, "mri", "rawavg.mgz"),
                    os.path.join(out_dir, "synthseg", s["fs_subject"]), threads)))
            elif s["stream"] == "cross" and not has("mri/rawavg.mgz"):
                item["missing"].append("synthseg: mri/rawavg.mgz absent")
        plan.append(item)
    # A long session uses its cross session's SynthSeg; say so when there is none.
    for item in plan:
        if synthseg and item["stream"] == "long" and \
                (item["subject"], item["session"]) not in synthseg_done:
            item["missing"].append("synthseg: no cross run of this session to segment")
    return plan


def check_paths(subjects_dir, out_dir):
    """The output must live outside the FreeSurfer folder (read-only here), and
    the other way round."""
    sd, out = Path(subjects_dir).resolve(), Path(out_dir).resolve()
    if out == sd or sd in out.parents or out in sd.parents:
        sys.exit(f"ERROR: --out ({out}) must be outside SUBJECTS_DIR ({sd}); "
                 "the FreeSurfer folder is read-only for this script.")


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

def run_tool(cmd, log):
    log.write("$ " + " ".join(cmd) + "\n")
    log.flush()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        log.write(f"  could not start: {exc}\n")
        return False, str(exc)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        log.write(f"  exit {proc.returncode}: {' | '.join(tail)}\n")
        return False, f"exit {proc.returncode}"
    return True, ""


def fmt(value, digits=4):
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def measure(item, out_dir, log, synthseg_vols):
    """Run one session's commands and read its files. Returns
    (summary row, wm rows, gw rows, synthseg rows)."""
    f = item["folder"]
    not_measured = list(item["missing"])
    ident = {k: item[k] for k in ("fs_subject", "subject", "session", "stream")}
    wm_rows, gw_rows, ss_rows = [], [], []
    summary = dict(ident)

    stamp_file = os.path.join(f, "scripts", "build-stamp.txt")
    summary["fs_build_stamp"] = (Path(stamp_file).read_text(errors="replace").strip()
                                 if os.path.isfile(stamp_file) else "n/a")

    wm = {}
    for name, cmd in item["commands"]:
        if name == "synthseg":
            ok, why = run_tool(cmd, log)
            vol_csv = cmd[cmd.index("--vol") + 1]
            if ok and os.path.isfile(vol_csv):
                synthseg_vols[(item["subject"], item["session"])] = synthseg_volumes(vol_csv)
            else:
                not_measured.append(f"synthseg: mri_synthseg failed ({why or 'no output'})")
            continue
        ok, why = run_tool(cmd, log)
        sum_file = cmd[cmd.index("--sum") + 1]
        if not ok or not os.path.isfile(sum_file):
            not_measured.append(f"{name}: mri_segstats failed ({why or 'no output'})")
            continue
        image = name.split("_", 1)[1]
        rows = wm_regions(parse_stats_table(sum_file))
        global_mean = pooled_mean(rows, set(DK.values()) | {"unsegmentedwm"})
        for r in rows:
            wm_rows.append(dict(ident, image=image, hemi=r["hemi"], region=r["region"],
                                label=r["label"], nvox=r["nvox"], mean=fmt(r["mean"]),
                                sd=fmt(r["sd"]),
                                mean_over_global_wm=fmt(r["mean"] / global_mean
                                                        if global_mean else None)))
        wm[image] = rows

    norm = wm.get("norm")
    poles = pooled_mean(norm, POLES) if norm else None
    centre = pooled_mean(norm, CENTRE) if norm else None
    summary["norm_wm_poles"] = fmt(poles)
    summary["norm_wm_centre"] = fmt(centre)
    summary["norm_wm_poles_minus_centre"] = fmt(poles - centre
                                                if poles is not None and centre is not None
                                                else None)
    off = outside_target(norm) if norm else None
    summary["norm_wm_n_outside_110pm5"] = fmt(len(off) if off is not None else None)
    summary["norm_wm_outside_110pm5"] = ";".join(off) if off else ("" if norm else "n/a")
    nu = wm.get("nu")
    nu_poles = pooled_mean(nu, POLES) if nu else None
    nu_centre = pooled_mean(nu, CENTRE) if nu else None
    summary["nu_wm_poles_over_centre"] = fmt(nu_poles / nu_centre
                                             if nu_poles and nu_centre else None)

    field = None
    if any(r.startswith("n4_field") for r in item["reads"]):
        try:
            field = field_percentiles(os.path.join(f, "mri", "nu.mgz"),
                                      os.path.join(f, "mri", "orig.mgz"),
                                      os.path.join(f, "mri", "brainmask.mgz"))
        except ImportError:
            not_measured.append("n4_field: nibabel/numpy not available (use fspython)")
        except Exception as exc:  # unreadable or mismatched volumes
            not_measured.append(f"n4_field: {exc}")
    for key in ("p2", "p50", "p98", "p98_over_p2"):
        summary[f"n4_field_{key}"] = fmt(field.get(key) if field else None)

    for hemi in ("lh", "rh"):
        path = os.path.join(f, "stats", f"{hemi}.w-g.pct.stats")
        if os.path.isfile(path):
            for r in gw_contrast(path, hemi):
                gw_rows.append(dict(ident, hemi=hemi, region=r["region"],
                                    nvert=r["nvert"], mean=fmt(r["mean"]),
                                    sd=fmt(r["sd"])))
    gw_for_pool = [{"region": r["region"], "mean": float(r["mean"]),
                    "nvert": r["nvert"]} for r in gw_rows if r["mean"] != "n/a"]
    gw_poles = pooled_mean(gw_for_pool, POLES, weight="nvert")
    gw_centre = pooled_mean(gw_for_pool, CENTRE, weight="nvert")
    summary["gw_contrast_poles"] = fmt(gw_poles)
    summary["gw_contrast_centre"] = fmt(gw_centre)
    summary["gw_contrast_poles_minus_centre"] = fmt(
        gw_poles - gw_centre if gw_poles is not None and gw_centre is not None else None)

    aseg_path = os.path.join(f, "stats", "aseg.stats")
    measures = parse_measures(aseg_path) if os.path.isfile(aseg_path) else {}
    for key, column in (("SurfaceHoles", "surface_holes"),
                        ("lhSurfaceHoles", "surface_holes_lh"),
                        ("rhSurfaceHoles", "surface_holes_rh")):
        value = measures.get(key)
        summary[column] = fmt(int(value) if value is not None else None)

    vols = synthseg_vols.get((item["subject"], item["session"]))
    if vols and os.path.isfile(aseg_path):
        aseg = {norm_name(str(r.get("StructName"))): r.get("Volume_mm3")
                for r in parse_stats_table(aseg_path)}
        for structure in SYNTHSEG_STRUCTURES:
            a, s = aseg.get(norm_name(structure)), vols.get(norm_name(structure))
            if a is None or s is None:
                not_measured.append(f"synthseg: {structure} missing in "
                                    f"{'aseg' if a is None else 'SynthSeg'}")
                continue
            ss_rows.append(dict(ident, structure=structure, aseg_mm3=fmt(a, 1),
                                synthseg_mm3=fmt(s, 1), diff_mm3=fmt(s - a, 1),
                                diff_pct=fmt(100.0 * (s - a) / a if a else None, 2)))

    summary["not_measured"] = " | ".join(not_measured)
    return summary, wm_rows, gw_rows, ss_rows


def write_tsv(path, rows, columns=None):
    columns = columns or (list(rows[0].keys()) if rows else [])
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, delimiter="\t",
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_dataset_description(out_dir, fs_stamp):
    desc = os.path.join(out_dir, "dataset_description.json")
    with open(desc, "w", encoding="utf-8") as f:
        json.dump({
            "Name": f"{paths.PROJECT['title']} intensity-inhomogeneity QC",
            "BIDSVersion": "1.9.0",
            "DatasetType": "derivative",
            "GeneratedBy": [
                {"Name": "qc_intensity.py",
                 "Description": "WM intensity (norm.mgz, nu.mgz) by wmparc region, "
                                "N4 field amplitude, grey/white contrast, "
                                "SurfaceHoles and SynthSeg-vs-aseg volumes, read "
                                "from existing recon-all runs."},
                {"Name": "freesurfer", "Version": fs_stamp or "unknown"}],
            "SourceDatasets": [{"Description": "bids/derivatives/freesurfer "
                                               "(recon-all cross and long runs)"}],
        }, f, indent="\t", ensure_ascii=False)
        f.write("\n")
    with open(os.path.join(out_dir, ".bidsignore"), "w", encoding="utf-8") as f:
        f.write("*.tsv\n*.txt\nsegstats/\nsynthseg/\n")


def print_plan(plan, subjects_dir, out_dir, bases, fs_stamp, stamps, synthseg_tool):
    print("=" * 72)
    print(" qc_intensity.py -- PLAN")
    print("=" * 72)
    print(f" project       : {paths.PROJECT_NAME}")
    print(f" SUBJECTS_DIR  : {subjects_dir}   (read-only)")
    print(f" output        : {out_dir}")
    print(f" FreeSurfer    : {fs_stamp or 'NOT LOADED (source FreeSurfer 8 first)'}")
    if stamps:
        print(f" recon versions: {len(stamps)} build-stamp(s) in SUBJECTS_DIR"
              + ("   <-- MIXED VERSIONS" if len(stamps) > 1 else ""))
        for stamp, subs in sorted(stamps.items()):
            print(f"     {stamp}: {len(subs)} folder(s)")
    print(f" mri_synthseg  : {synthseg_tool}")
    print(f" sessions      : {len(plan)} "
          f"({sum(i['stream'] == 'cross' for i in plan)} cross, "
          f"{sum(i['stream'] == 'long' for i in plan)} long); "
          f"base templates skipped: {len(bases)}")
    for item in plan:
        print(f"\n [{item['stream']}] {item['fs_subject']}")
        for name, cmd in item["commands"]:
            print(f"   run  {name:10s} {' '.join(cmd)}")
        for what in item["reads"]:
            print(f"   read {what}")
        for what in item["missing"]:
            print(f"   NOT MEASURED  {what}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Intensity-inhomogeneity QC on existing FreeSurfer runs "
                    "(read-only on SUBJECTS_DIR).")
    parser.add_argument("--subjects-dir", default=str(paths.FREESURFER_DIR),
                        help="FreeSurfer SUBJECTS_DIR to read (default: "
                             "bids/derivatives/freesurfer).")
    parser.add_argument("--out", default=str(paths.BIDS_ROOT / "derivatives"
                                             / "qc-intensidade"),
                        help="Output folder (default: bids/derivatives/qc-intensidade).")
    parser.add_argument("--subjects", nargs="*", default=[],
                        help="Only these subjects (e.g. sub-CBR003).")
    parser.add_argument("--no-synthseg", action="store_true",
                        help="Skip measure (e), the SynthSeg comparison.")
    parser.add_argument("--threads", type=int, default=4,
                        help="Threads for mri_synthseg (default: 4).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show the plan and exit; runs and writes nothing.")
    args = parser.parse_args()

    subjects_dir, out_dir = os.path.abspath(args.subjects_dir), os.path.abspath(args.out)
    check_paths(subjects_dir, out_dir)
    sessions = find_sessions(subjects_dir, set(args.subjects))
    fs_stamp = rrl.fs_build_stamp(os.environ.get("FREESURFER_HOME"))
    stamps = rrl.stamps_in(subjects_dir) if os.path.isdir(subjects_dir) else {}
    synthseg_tool = shutil.which("mri_synthseg")
    use_synthseg = not args.no_synthseg
    plan = build_plan(sessions, out_dir, synthseg=use_synthseg, threads=args.threads)
    if use_synthseg and not synthseg_tool:
        for item in plan:
            item["commands"] = [c for c in item["commands"] if c[0] != "synthseg"]
            item["missing"] = [m for m in item["missing"] if not m.startswith("synthseg")]
            item["missing"].append("synthseg: mri_synthseg not found")
    print_plan(plan, subjects_dir, out_dir, base_templates(subjects_dir), fs_stamp,
               stamps, synthseg_tool or ("skipped (--no-synthseg)" if args.no_synthseg
                                         else "not found"))

    lock_path = str(paths.BATCH_LOCK)
    if args.dry_run:
        if batch_lock.is_held(lock_path):
            h = batch_lock.holder(lock_path)
            print(f" NOTE: a batch holds the lock now (project {h.get('project', '?')}, "
                  f"since {h.get('started', '?')}); a real run would refuse to start.")
        print(" DRY RUN: nothing was run or written.")
        return
    if not sessions:
        sys.exit(f"ERROR: no finished FreeSurfer session in {subjects_dir}.")
    if not shutil.which("mri_segstats"):
        sys.exit("ERROR: 'mri_segstats' not found. Source FreeSurfer 8 first.")

    lock = batch_lock.acquire(lock_path, {"project": paths.PROJECT_NAME,
                                          "pid": os.getpid(), "script": "qc_intensity.py",
                                          "started": datetime.now().isoformat(
                                              timespec="seconds")})
    if lock is None:
        h = batch_lock.holder(lock_path)
        print(f"A BATCH IS RUNNING (project {h.get('project', '?')}, pid "
              f"{h.get('pid', '?')}, since {h.get('started', '?')}). Not starting.")
        sys.exit(3)
    try:
        os.makedirs(os.path.join(out_dir, "segstats"), exist_ok=True)
        summaries, wm_all, gw_all, ss_all = [], [], [], []
        synthseg_vols = {}
        # cross first, so each long session finds its cross SynthSeg volumes
        ordered = sorted(plan, key=lambda i: (i["stream"] != "cross", i["fs_subject"]))
        with open(os.path.join(out_dir, "qc_intensity_log.txt"), "w",
                  encoding="utf-8") as log:
            log.write(f"qc_intensity.py {datetime.now().isoformat(timespec='seconds')}\n"
                      f"FreeSurfer: {fs_stamp or 'unknown'}\n"
                      f"recon build-stamps: {json.dumps({k: len(v) for k, v in stamps.items()})}\n\n")
            for item in ordered:
                print(f" measuring {item['fs_subject']} ...", flush=True)
                for name, cmd in item["commands"]:
                    if name == "synthseg":
                        os.makedirs(os.path.dirname(cmd[cmd.index("--o") + 1]),
                                    exist_ok=True)
                summary, wm_rows, gw_rows, ss_rows = measure(item, out_dir, log,
                                                             synthseg_vols)
                summaries.append(summary)
                wm_all += wm_rows
                gw_all += gw_rows
                ss_all += ss_rows
        summaries.sort(key=lambda r: r["fs_subject"])
        write_tsv(os.path.join(out_dir, "sessions.tsv"), summaries)
        write_tsv(os.path.join(out_dir, "wm_intensity.tsv"), wm_all,
                  ["fs_subject", "subject", "session", "stream", "image", "hemi",
                   "region", "label", "nvox", "mean", "sd", "mean_over_global_wm"])
        write_tsv(os.path.join(out_dir, "gw_contrast.tsv"), gw_all,
                  ["fs_subject", "subject", "session", "stream", "hemi", "region",
                   "nvert", "mean", "sd"])
        write_tsv(os.path.join(out_dir, "synthseg_vs_aseg.tsv"), ss_all,
                  ["fs_subject", "subject", "session", "stream", "structure",
                   "aseg_mm3", "synthseg_mm3", "diff_mm3", "diff_pct"])
        write_dataset_description(out_dir, fs_stamp)
    finally:
        lock.close()
    incomplete = sum(1 for r in summaries if r["not_measured"])
    print(f"\n DONE: {len(summaries)} session(s) -> {out_dir}")
    if incomplete:
        print(f" {incomplete} session(s) with measures not taken: see the "
              "'not_measured' column of sessions.tsv")


if __name__ == "__main__":
    main()
