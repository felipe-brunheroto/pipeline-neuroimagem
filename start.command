#!/bin/bash
# =============================================================================
#  PLUG AND PLAY - the whole pipeline from one double-click (macOS)
#
#  First time only, in Terminal (the repository is private, so git asks you to
#  sign in to GitHub):
#      git clone https://github.com/felipe-brunheroto/pipeline-neuroimagem.git ~/pipeline-neuroimagem
#      bash ~/pipeline-neuroimagem/start.command
#  After that: double-click start.command, or just drop new data in inbox/ and
#  let the nightly run take it.
#
#  Every time it:
#    1. checks GitHub for new code, shows what changed and updates only if you
#       say yes (this Mac may hold patient files: new code is never run
#       unseen; --yes never updates). Data folders are never in git.
#    2. prepares Python: .venv/ with requirements.txt (needs Python 3.10+)
#    3. loads FreeSurfer ($FREESURFER_HOME, else the newest /Applications/freesurfer/*)
#    4. imports new data - DICOM CDs, dcm2bids BIDS, finished FreeSurfer runs,
#       or a whole old copy of the pipeline - each into ITS project, decided by
#       the code in its name (CBR... -> CBR, GER... -> COGAGING):
#         inbox/ is moved into place; folders you drag in are copied
#    5. processes every project now (code/pipeline.py run), if you want
#    6. offers to repeat 4-5 every night (launchd)
#
#  Options: folders to import; --yes (no questions); --no-run (stop after 4).
#  Nothing here sends data anywhere: GitHub only ever sees the code.
# =============================================================================
cd "$(dirname "$0")" || exit 1
REPO="$(pwd)"
YES=0; RUN=1; FOLDERS=()
for arg in "$@"; do
    case "$arg" in
        --yes) YES=1 ;;
        --no-run) RUN=0 ;;
        *) FOLDERS+=("$arg") ;;
    esac
done
ask() {   # ask "question" -> 0 for yes
    [ "$YES" = 1 ] && return 0
    read -r -p "  $1 (y/n) " reply
    [[ "$reply" =~ ^[YySs] ]]
}
step() { echo; echo "== $1"; }

step "1/6  Code updates from GitHub"
if [ ! -d .git ]; then
    echo "  (not a git clone: not updating)"
elif ! GIT_TERMINAL_PROMPT=0 git fetch -q 2>/dev/null </dev/null; then
    # Never stop to ask for a GitHub login: on a shared Mac the account is
    # signed out after setup (gh auth logout), and that must not block a run.
    echo "  (could not reach GitHub - offline or signed out? using this copy)"
elif [ -z "$(git log --oneline HEAD..@{u} 2>/dev/null)" ]; then
    echo "  up to date"
else
    # Show what would change before running it on this machine's data.
    echo "  New code on GitHub:"
    git log --oneline HEAD..@{u} | sed 's/^/    /'
    if [ "$YES" = 1 ]; then
        echo "  (--yes: not updating; run without --yes to review and update)"
    elif ask "Update to this code?"; then
        git pull --ff-only -q || echo "  (could not update - local changes? using this copy)"
    else
        echo "  keeping the current code"
    fi
fi

step "2/6  Python"
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 \
            /usr/local/bin/python3 python3; do
    if command -v "$cand" >/dev/null 2>&1 &&
       "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PY="$(command -v "$cand")"; break
    fi
done
if [ -z "$PY" ]; then
    echo "  Python 3.10 or newer is needed (the one that comes with macOS is 3.9)."
    echo "  Install it from https://www.python.org/downloads/macos/ and run this again."
    exit 1
fi
if [ ! -x .venv/bin/python ]; then
    echo "  creating .venv/ with $("$PY" --version)"
    "$PY" -m venv .venv || exit 1
fi
if ! cmp -s requirements.txt .venv/requirements.installed; then
    echo "  installing requirements.txt ..."
    .venv/bin/python -m pip install -q --upgrade pip &&
    .venv/bin/python -m pip install -q -r requirements.txt pytest &&
    cp requirements.txt .venv/requirements.installed || exit 1
fi
PYV=.venv/bin/python
echo "  ready: $($PYV --version)"

step "3/6  FreeSurfer"
if [ -z "$FREESURFER_HOME" ]; then
    FREESURFER_HOME="$($PYV - <<'EOF'
import glob, os, re
homes = [os.path.dirname(p) for p in glob.glob("/Applications/freesurfer/*/SetUpFreeSurfer.sh")]
key = lambda h: [int(x) for x in re.findall(r"\d+", os.path.basename(h))] or [0]
print(max(homes, key=key) if homes else "")
EOF
)"
fi
NOFS=()
if [ -n "$FREESURFER_HOME" ] && [ -f "$FREESURFER_HOME/SetUpFreeSurfer.sh" ]; then
    export FREESURFER_HOME
    # shellcheck disable=SC1091
    source "$FREESURFER_HOME/SetUpFreeSurfer.sh" >/dev/null 2>&1
    echo "  loaded: $FREESURFER_HOME"
else
    NOFS=(--no-freesurfer)
    echo "  FreeSurfer not found: conversion and BIDS only (no recon / SAMSEG)."
fi

step "4/6  Importing new data"
if [ ${#FOLDERS[@]} -eq 0 ] && [ "$YES" = 0 ] && [ -t 0 ]; then
    echo "  Drag folders to import into this window (one per line: CDs, BIDS,"
    echo "  FreeSurfer runs, an old copy of the pipeline), then ENTER on an empty"
    echo "  line. Just ENTER: only what is in inbox/."
    while read -r -p "  folder> " line && [ -n "$line" ]; do
        line="${line%\"}"; line="${line#\"}"; line="${line%\'}"; line="${line#\'}"
        line="${line//\\ / }"; line="${line%"${line##*[![:space:]]}"}"
        FOLDERS+=("$line")
    done
fi
$PYV code/pipeline.py import --dry-run --show-paths
[ ${#FOLDERS[@]} -gt 0 ] && $PYV code/pipeline.py import --dry-run --show-paths "${FOLDERS[@]}"
if ask "Import as shown above?"; then
    $PYV code/pipeline.py import --show-paths
    [ ${#FOLDERS[@]} -gt 0 ] && $PYV code/pipeline.py import --show-paths "${FOLDERS[@]}"
else
    echo "  nothing imported"
fi

if [ "$RUN" = 1 ]; then
    step "5/6  Processing"
    $PYV code/pipeline.py status
    echo "  Processing everything new can take hours (recon-all ~1h30 per exam);"
    echo "  keep this window open, the Mac stays awake by itself."
    if ask "Process now? (n = leave it for the nightly run)"; then
        $PYV code/pipeline.py run --show-paths "${NOFS[@]}"
    fi
fi

step "6/6  Every night"
PLIST="$HOME/Library/LaunchAgents/org.pipeline-neuroimagem.nightly.plist"
if [ -f "$PLIST" ]; then
    echo "  already scheduled ($PLIST)"
    echo "  (to cap recon-all at night: $PYV code/pipeline.py schedule --at 22:30 --jobs 2)"
elif [ "$YES" = 0 ] && ask "Import inbox/ and process every project every night at 22:30?"; then
    # Each recon-all takes ~8 GB. On a Mac shared with other heavy work (another
    # study's own scripts, Docker), cap how many run at once.
    JOBS=()
    read -r -p "  How many recon-all at once? (ENTER = as many as fit; 2 if this Mac is shared) " n
    [[ "$n" =~ ^[1-9][0-9]*$ ]] && JOBS=(--jobs "$n")
    $PYV code/pipeline.py schedule --at 22:30 "${JOBS[@]}" "${NOFS[@]}"
else
    echo "  not scheduled (later: .venv/bin/python code/pipeline.py schedule --at 22:30)"
fi
echo
echo "Done. Summaries: projects/<NAME>/logs/batch/last_summary.md - students: Pipeline_Alunos/iniciar.command"
echo "Something looks wrong? Pipeline_Alunos/diagnostico.command checks this Mac and explains each item."
