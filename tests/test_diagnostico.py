"""diagnostico.py: explains the Mac without ever printing a patient name.

Synthetic projects, a stub FreeSurfer, a temporary HOME (for LaunchAgents) and
stub launchctl/pmset. --offline: no GitHub; --sem-perguntas: no prompts.
"""

import plistlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic as s  # noqa: E402

ARGS = ("--offline", "--sem-perguntas")


def make_env(tmp_path, license=True, wake=True):
    projects = s.make_projects(tmp_path)
    fs = s.make_freesurfer(tmp_path, license=license)
    s.fake_bin(tmp_path, "launchctl", "exit 0")
    fakebin = s.fake_bin(tmp_path, "pmset",
                         'echo "Repeating power events:"; '
                         + ('echo "  wakepoweron at 10:25PM every day"' if wake else "true"))
    home = tmp_path / "home"
    (home / "Library" / "LaunchAgents").mkdir(parents=True)
    return s.env_for(projects, fs, extra_path=[fakebin], HOME=home), projects, home


def write_job(home, name, repo, fs_home):
    plist = home / "Library" / "LaunchAgents" / f"org.pipeline-neuroimagem.{name}.plist"
    script = f"export FREESURFER_HOME={fs_home}; cd '{repo}'; exec python code/pipeline.py run"
    plist.write_bytes(plistlib.dumps({
        "Label": f"org.pipeline-neuroimagem.{name}",
        "ProgramArguments": ["/bin/bash", "-c", script],
        "StartCalendarInterval": {"Hour": 22, "Minute": 30}}))


def test_counts_exams_but_never_prints_their_names(tmp_path):
    env, projects, home = make_env(tmp_path)
    s.make_exam(str(projects / "COGAGING" / "Patient Files" / s.exam_folder_name("GER00039", "20240101")),
                s.COG_SERIES, "20240101")
    write_job(home, "nightly", s.REPO, env["FREESURFER_HOME"])
    r = s.run("diagnostico.py", "CBR", env, *ARGS)
    out = r.stdout + r.stderr
    assert "QUIXOTEFAKE" not in out and "ZEBULON" not in out
    assert "COGAGING: 1 exame(s) em Patient Files" in out
    assert "Licenca do FreeSurfer:" in out and "todo dia as 22:30" in out


def test_missing_license_is_explained_and_fails(tmp_path):
    env, _projects, _home = make_env(tmp_path, license=False)
    r = s.run("diagnostico.py", "CBR", env, *ARGS)
    assert r.returncode == 1
    assert "[ FALTA] Licenca do FreeSurfer" in r.stdout
    assert "registration.html" in r.stdout


def test_job_of_another_copy_and_no_wake_are_flagged(tmp_path):
    env, _projects, home = make_env(tmp_path, wake=False)
    write_job(home, "nightly", tmp_path / "old copy", env["FREESURFER_HOME"])
    write_job(home, "batch.cbr", s.REPO, env["FREESURFER_HOME"])
    out = s.run("diagnostico.py", "CBR", env, *ARGS).stdout
    assert "aponta para OUTRA copia do pipeline" in out
    assert "agendamento antigo, por estudo" in out
    assert "NAO esta programado para acordar" in out
    assert "sudo pmset repeat wakeorpoweron" in out


def test_no_schedule_is_missing(tmp_path):
    env, _projects, _home = make_env(tmp_path)
    r = s.run("diagnostico.py", "CBR", env, *ARGS)
    assert "[ FALTA] Agendamento: nenhum processamento agendado" in r.stdout
    assert r.returncode == 1


def test_shows_the_recon_cap_of_the_schedule(tmp_path):
    env, _projects, home = make_env(tmp_path)
    write_job(home, "nightly", s.REPO, env["FREESURFER_HOME"])
    plist = home / "Library" / "LaunchAgents" / "org.pipeline-neuroimagem.nightly.plist"
    job = plistlib.loads(plist.read_bytes())
    job["ProgramArguments"][2] += " --wait 720 --jobs 2"
    plist.write_bytes(plistlib.dumps(job))
    out = s.run("diagnostico.py", "CBR", env, *ARGS).stdout
    assert "todo dia as 22:30, ate 2 FreeSurfer ao mesmo tempo" in out
