"""
synthetic.py
============

Reusable helpers for the self-tests: a throw-away projects/ folder with
synthetic DICOM exams (written with pydicom at test time, never committed), and
stub FreeSurfer commands on PATH. Nothing here ever touches real data: every
helper writes under the temporary folder it is given, and the pipeline is
pointed at it with PIPELINE_PROJECTS_DIR.

The fake identifiers below are deliberately odd strings; the leak test greps
every output and log for them.
"""

import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid

REPO = Path(__file__).resolve().parent.parent
CODE = REPO / "code"
STUB = Path(__file__).resolve().parent / "fs_stub.py"

# Identifier values planted in every fake DICOM. None may appear in any output.
FAKE = {
    "PatientName": "QUIXOTEFAKE^ZEBULON",
    "PatientID": "PIDX9182736",
    "PatientBirthDate": "19391231",
    "AccessionNumber": "ACCN7766554",
    "InstitutionName": "HOSPITAL FICTICIO ZYXW",
    "InstitutionAddress": "RUA INVENTADA 999",
    "StationName": "STATIONQWERTY",
    "DeviceSerialNumber": "SERIAL5550123",
    "ReferringPhysicianName": "DRFAKE^NOBODYX",
    "StudyDescription": "CRANIO^PESQUISA HOSPITAL FICTICIO ZYXW",
}
# Strings to search for: the values above plus the name pieces used in the
# COGAGING folder names (see exam_folder_name).
LEAK_STRINGS = sorted({v for v in FAKE.values()} | {
    "QUIXOTEFAKE", "ZEBULON", "ZYXW", "FICTICIO", "PIDX9182736", "ACCN7766554"})

# Series every synthetic exam carries: (SeriesDescription, 3D?, n_slices).
CBR_SERIES = [("AAHead_Scout", False, 3),
              ("t1_mprage_sag_p2_iso", True, 6),
              ("t2_space_da-fl3_sag", True, 6),
              ("asl_3d_1800", False, 2),
              ("gre_field_mapping", False, 2)]
COG_SERIES = [("survey", False, 3),
              ("T1 3D MPRAGE SAG", True, 6),
              ("3D FLAIR SAG", True, 6)]
UNKNOWN_SERIES = ("zzq_mystery_seq", False, 2)


def write_series(folder, description, is_3d, n_slices, number, study, rows=12):
    """One DICOM series (n_slices files) in `folder`. `study` carries the
    StudyInstanceUID / StudyDate shared by the exam."""
    os.makedirs(folder, exist_ok=True)
    series_uid = generate_uid()
    for k in range(n_slices):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = MRImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds = Dataset()
        ds.file_meta = meta
        ds.SOPClassUID = MRImageStorage
        ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        ds.Modality = "MR"
        ds.Manufacturer = "SIEMENS"
        ds.ManufacturerModelName = "Skyra"
        ds.MagneticFieldStrength = 3
        for tag, value in FAKE.items():
            setattr(ds, tag, value)
        ds.StudyInstanceUID = study["uid"]
        ds.StudyDate = ds.SeriesDate = ds.AcquisitionDate = ds.ContentDate = study["date"]
        ds.StudyTime = ds.SeriesTime = ds.AcquisitionTime = "101500"
        ds.SeriesInstanceUID = series_uid
        ds.SeriesNumber = number
        ds.SeriesDescription = description
        ds.ProtocolName = description
        ds.InstanceNumber = k + 1
        ds.ImageType = ["ORIGINAL", "PRIMARY", "M", "ND"]
        ds.MRAcquisitionType = "3D" if is_3d else "2D"
        ds.RepetitionTime = 2300.0
        ds.EchoTime = 2.98
        ds.FlipAngle = 9
        ds.SliceThickness = 1.0
        ds.PixelSpacing = [1.0, 1.0]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.ImagePositionPatient = [0.0, 0.0, float(k)]
        ds.SliceLocation = float(k)
        ds.Rows = ds.Columns = rows
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = 16
        ds.BitsStored = 12
        ds.HighBit = 11
        ds.PixelRepresentation = 0
        pixels = (np.arange(rows * rows, dtype=np.uint16).reshape(rows, rows)
                  + 7 * k + number) % 4000
        ds.PixelData = pixels.astype(np.uint16).tobytes()
        ds.save_as(os.path.join(folder, f"IM{k + 1:04d}"), enforce_file_format=True)


def make_exam(exam_dir, series, date, unknown=False, age_seconds=7200):
    """A fake CD: DICOM/<series>/IM* plus scanner junk (DICOMDIR-like files).
    Files are aged by `age_seconds` so the batch's settle check treats the
    folder as fully copied."""
    study = {"uid": generate_uid(), "date": date}
    all_series = list(series) + ([UNKNOWN_SERIES] if unknown else [])
    for number, (desc, is_3d, n) in enumerate(all_series, start=1):
        write_series(os.path.join(exam_dir, "DICOM", f"S{number}"), desc, is_3d,
                     n, number, study)
    with open(os.path.join(exam_dir, "AUTORUN.INF"), "w") as f:
        f.write("[autorun]\n")
    if age_seconds:
        age(exam_dir, age_seconds)
    return study


def age(folder, seconds):
    """Set every file's mtime `seconds` into the past."""
    past = time.time() - seconds
    for root, dirs, files in os.walk(folder):
        for name in files + dirs:
            os.utime(os.path.join(root, name), (past, past))
    os.utime(folder, (past, past))


def exam_folder_name(code, date):
    """A COGAGING-style CD folder name: the study code plus a fake patient
    name and the exam date (the parts that must never be written out)."""
    return f"{code}_QUIXOTEFAKE_ZEBULON_{date}"


def make_projects(root):
    """A projects/ folder with copies of the real CBR and COGAGING settings
    (project.json + series_map.csv) and no data. Returns its path."""
    projects = Path(root) / "projects"
    for name in ("CBR", "COGAGING"):
        dest = projects / name
        dest.mkdir(parents=True)
        for f in ("project.json", "series_map.csv"):
            src = REPO / "projects" / name / f
            if src.is_file():
                shutil.copy(src, dest / f)
        (dest / "Patient Files").mkdir()
    return projects


# ---------------------------------------------------------------------------
# Stub FreeSurfer
# ---------------------------------------------------------------------------
FS_TOOLS = ["recon-all", "mri_coreg", "mri_vol2vol", "run_samseg", "mri_binarize",
            "mideface", "asegstats2table", "aparcstats2table", "freeview"]


def make_freesurfer(root, license=True, version="freesurfer-darwin-macOS-arm64-8.2.0-stub"):
    """A fake FREESURFER_HOME whose bin/ holds one shell wrapper per tool, each
    calling fs_stub.py. Returns the FREESURFER_HOME path."""
    home = Path(root) / "freesurfer"
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    (home / "build-stamp.txt").write_text(version + "\n")
    if license:
        (home / "license.txt").write_text("stub@example.org\n12345\n *AbCdEf\n")
    # What start.command / launchd source to load it: put bin/ on PATH.
    (home / "SetUpFreeSurfer.sh").write_text('export PATH="$FREESURFER_HOME/bin:$PATH"\n'
                                             'export SUBJECTS_DIR="$FREESURFER_HOME/subjects"\n')
    for tool in FS_TOOLS:
        path = bin_dir / tool
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{STUB}" {tool} "$@"\n')
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return home


def fake_bin(root, name, script):
    """Put a small shell script called `name` in <root>/fakebin (e.g. a stub
    caffeinate or launchctl). Returns the folder."""
    folder = Path(root) / "fakebin"
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n" + script + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return folder


def env_for(projects, fs_home=None, extra_path=(), **extra):
    """Environment for running pipeline scripts against the synthetic projects."""
    env = dict(os.environ)
    env["PIPELINE_PROJECTS_DIR"] = str(projects)
    env.pop("PIPELINE_PROJECT", None)
    env.pop("SUBJECTS_DIR", None)
    env.pop("FS_LICENSE", None)
    env.pop("FREESURFER_HOME", None)
    path = [str(p) for p in extra_path]
    if fs_home:
        env["FREESURFER_HOME"] = str(fs_home)
        path.append(str(Path(fs_home) / "bin"))
        # SetUpFreeSurfer.sh points SUBJECTS_DIR at the FreeSurfer install;
        # the batch must not follow it.
        env["SUBJECTS_DIR"] = str(Path(fs_home) / "subjects")
    env["PATH"] = os.pathsep.join(path + [env.get("PATH", "")])
    env.update({k: str(v) for k, v in extra.items()})
    return env


def run(script, project, env, *args, code_dir=CODE, timeout=600):
    """Run `python code/<script> --project NAME args...`; returns CompletedProcess."""
    cmd = [sys.executable, str(Path(code_dir) / script), "--project", project, *args]
    return subprocess.run(cmd, env=env, capture_output=True, text=True,
                          timeout=timeout, cwd=str(REPO))


def text_files(root):
    """Every file under root, with its content as text (gzip members unpacked,
    other binaries decoded leniently) - for the leak check."""
    import gzip
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if path.name.endswith(".gz"):
            try:
                data = gzip.decompress(data)
            except OSError:
                pass
        yield path, data.decode("latin-1")
