"""
Self-tests for the student menu's new items (Pipeline_Alunos/passos.py):
R (last batch summary), C (QC walk-through), S (teach a rule for an unknown
series), and the refusal to run a step by hand while a batch runs. Synthetic
data only, under a temporary PIPELINE_PROJECTS_DIR (the menu then keeps its
remembered choices there too, never in Pipeline_Alunos/).

    python -m pytest tests/test_menu.py -q
"""

import csv
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic as s  # noqa: E402

sys.path.insert(0, str(s.CODE))
import batch_lock  # noqa: E402
import series_classifier as sc  # noqa: E402

MENU = s.REPO / "Pipeline_Alunos" / "passos.py"
FAST = ["--min-free-gb", "0", "--no-caffeinate"]


def load_menu():
    spec = importlib.util.spec_from_file_location("passos_under_test", MENU)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def done(tmp_path):
    """COGAGING after one batch run: QC to do and one unknown series."""
    projects = s.make_projects(tmp_path)
    pf = projects / "COGAGING" / "Patient Files"
    s.make_exam(str(pf / s.exam_folder_name("GER00039", "20120304")), s.COG_SERIES,
                "20120304", unknown=True)
    fs = s.make_freesurfer(tmp_path)
    env = s.env_for(projects, fs)
    r = s.run("run_batch.py", "COGAGING", env, *FAST)
    assert r.returncode == 0, r.stdout + r.stderr
    return {"projects": projects, "env": env, "project": projects / "COGAGING"}


def menu(ctx, keys):
    """Run the menu with the given key presses (one per line)."""
    return subprocess.run([sys.executable, str(MENU)], input="\n".join(keys) + "\n",
                          env=ctx["env"], capture_output=True, text=True, timeout=120,
                          cwd=str(s.REPO))


UNKNOWN = {"series_description": "zzq_mystery_seq", "image_type": "ORIGINAL/PRIMARY/M/ND",
           "modality": "MR", "sequence": ""}


def rule(**kw):
    base = {"match_type": "exact", "pattern": "zzq_mystery_seq", "datatype": "misc",
            "suffix": "", "entities": "", "derived": "", "notes": "test"}
    return dict(base, **kw)


def test_rule_validation(tmp_path):
    m = load_menu()
    empty_map = tmp_path / "series_map.csv"
    ok = lambda r: m.validar_regra(r, UNKNOWN, empty_map)[0]
    assert ok(rule()) == []
    assert ok(rule(datatype="anat", suffix="T1w")) == []
    assert ok(rule(datatype="anat", suffix="bold"))                 # wrong suffix
    assert ok(rule(datatype="nonsense"))
    assert ok(rule(pattern="t1_mprage"))                            # does not match
    assert ok(rule(match_type="regex", pattern="zzq_(mystery"))     # bad regex
    assert ok(rule(match_type="substring", pattern="zzq 20120304")) # date-like
    assert ok(rule(match_type="substring", pattern="# zzq"))        # would be a comment
    assert ok(rule(datatype="func", suffix="bold", entities="task=r-e-s-t"))
    assert ok(rule(datatype="func", suffix="bold", entities="color=rest"))
    errors, warnings = m.validar_regra(rule(datatype="func", suffix="bold"), UNKNOWN,
                                       empty_map)
    assert errors == [] and any("task=" in w for w in warnings)
    # An earlier rule of the study that already sends the series elsewhere wins.
    empty_map.write_text(m.CABECALHO_MAPA + "\nsubstring,mystery,unknown,,,,\n")
    assert any("outra regra" in e for e in ok(rule()))


def test_rule_is_appended_and_used(tmp_path):
    m = load_menu()
    path = tmp_path / "series_map.csv"                               # a study without one
    assert m.gravar_regra(path, rule(notes="localizer, with a comma"), "AB")
    assert m.gravar_regra(path, rule(pattern="other_seq", datatype="anat",
                                     suffix="T2w"), "")
    text = path.read_text()
    assert text.startswith(m.CABECALHO_MAPA)
    assert "# added from the student menu on" in text and " by AB" in text
    rules = sc.load_series_map(str(path))
    assert [r["pattern"] for r in rules] == ["zzq_mystery_seq", "other_seq"]
    assert rules[0]["notes"] == "localizer, with a comma"
    assert sc.classify("zzq_mystery_seq", series_map=rules).datatype == "misc"


def test_menu_summary_qc_and_new_rule(done):
    # C: approve the first item, fail the second, then leave.
    r = menu(done, ["2", "r", "", "c", "AB", "", "a", "ok", "", "r",
                    "pial cuts the left temporal lobe", "s", "", "q"])
    out = r.stdout
    assert "RESUMO DA ULTIMA RODADA AUTOMATICA" in out
    assert "terminou bem" in out and "use o item S" in out
    assert "QC para fazer: 1 recon do FreeSurfer, 1 mascara(s) do SAMSEG" in out
    qc = list(csv.DictReader(open(done["project"] / "bids/derivatives/qc/qc_status.tsv"),
                             delimiter="\t"))
    assert [(q["kind"], q["status"], q["reviewer"]) for q in qc] == \
        [("recon", "pass", "AB"), ("samseg", "fail", "AB")]

    # S: the unknown series is a localizer -> misc, exact name.
    # (the study is remembered from now on: no "which study?" question)
    # type 7 (misc: no suffix to ask), no entities, not derived, exact name.
    r = menu(done, ["s", "1", "7", "", "", "", "localizer", "s", "AB", "", "q"])
    assert "Regra gravada" in r.stdout, r.stdout[-3000:]
    assert "exact,zzq_mystery_seq,misc" in (done["project"] / "series_map.csv").read_text()

    # The next batch converts that exam again and the series is no longer unknown.
    b = s.run("run_batch.py", "COGAGING", done["env"], *FAST)
    assert b.returncode == 0 and "1 reclassify" in b.stdout, b.stdout
    report = done["project"] / "bids/sourcedata/conversion/reports/unknown_series_report.csv"
    assert len(list(csv.DictReader(open(report)))) == 0
    r = menu(done, ["r", "", "q"])
    assert "Reprovados no QC" in r.stdout


def test_menu_refuses_manual_steps_while_a_batch_runs(done):
    lock = batch_lock.acquire(str(done["projects"] / ".batch.lock"),
                              {"project": "COGAGING", "started": "2026-01-01T02:00:00"})
    try:
        r = menu(done, ["2", "6", "", "q"])
    finally:
        lock.close()
    assert "RODANDO agora" in r.stdout
    assert "Nao rode passos a mao ao mesmo tempo" in r.stdout
    assert "Deseja rodar este passo agora" not in r.stdout


def test_menu_lists_both_projects_without_touching_the_repo(done):
    before = sorted(p.name for p in (s.REPO / "Pipeline_Alunos").iterdir())
    r = subprocess.run([sys.executable, str(MENU), "--list"], env=done["env"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "estudo CBR" in r.stdout and "estudo COGAGING" in r.stdout
    assert "R) Resumo da ultima rodada automatica" in r.stdout
    menu(done, ["2", "q"])
    assert sorted(p.name for p in (s.REPO / "Pipeline_Alunos").iterdir()) == before
    assert (done["projects"] / "projeto_atual.txt").read_text().strip() == "COGAGING"


def test_menu_recon_ignores_the_freesurfer_subjects_dir(tmp_path):
    """Step 6 by hand must write into bids/derivatives/freesurfer even though
    loading FreeSurfer points SUBJECTS_DIR at its own install folder."""
    projects = s.make_projects(tmp_path)
    pf = projects / "COGAGING" / "Patient Files"
    s.make_exam(str(pf / s.exam_folder_name("GER00039", "20120304")), s.COG_SERIES,
                "20120304")
    fs = s.make_freesurfer(tmp_path)
    env = s.env_for(projects, fs)           # sets SUBJECTS_DIR=<fs>/subjects
    r = s.run("run_batch.py", "COGAGING", env, *FAST, "--no-freesurfer")
    assert r.returncode == 0, r.stdout + r.stderr
    ctx = {"env": env}
    r = menu(ctx, ["2", "6", "s", "1", "", "q"])
    assert "Passo concluido" in r.stdout, r.stdout[-3000:]
    fsdir = projects / "COGAGING" / "bids" / "derivatives" / "freesurfer"
    assert (fsdir / "sub-GER00039_ses-1" / "scripts" / "recon-all.done").is_file()
    assert not (fs / "subjects").exists()
