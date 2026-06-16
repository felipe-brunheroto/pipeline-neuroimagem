#!/usr/bin/env bash
# =============================================================================
# scan_download.sh  —  SAFE, read-only check of a downloaded tool.
#
# Accepts EITHER a .zip file OR an already-extracted folder (macOS / Safari
# often auto-unzips downloads into a folder). It ONLY inspects; it NEVER runs
# the tool, and never touches anything outside the target you give it.
#
# USAGE:
#   ./scan_download.sh  MINDSETS.zip          [expected_sha256]
#   ./scan_download.sh  MINDSETS-BrainSeg/    [expected_sha256]
#
# The optional second argument (only meaningful for a .zip) is the SHA-256 the
# MBZUAI team gave you; if you pass it, the script confirms the download is intact.
# =============================================================================
set -euo pipefail

TARGET="${1:-}"
EXPECTED="${2:-}"

line() { printf '%s\n' "----------------------------------------------------------------------"; }

if [[ -z "$TARGET" || ! -e "$TARGET" ]]; then
  echo "Usage: ./scan_download.sh  <file.zip | extracted-folder>  [expected_sha256]"
  exit 1
fi

# Decide mode: a folder is scanned in place; a zip is checksummed + extracted.
if [[ -d "$TARGET" ]]; then
  MODE="folder"; SANDBOX="$TARGET"
else
  MODE="zip"; SANDBOX="extracted"
fi

echo "======================================================================"
echo " SAFE DOWNLOAD CHECK (read-only — does NOT run the tool)"
echo " target: $TARGET   (mode: $MODE)"
echo " when  : $(date)"
echo "======================================================================"

if [[ "$MODE" == "zip" ]]; then
  # 1) Checksum ---------------------------------------------------------------
  line; echo "1) SHA-256 checksum"
  GOT="$(shasum -a 256 "$TARGET" | awk '{print $1}')"
  echo "   computed: $GOT"
  if [[ -n "$EXPECTED" ]]; then
    if [[ "$GOT" == "$EXPECTED" ]]; then
      echo "   RESULT: ✅ MATCH — download is intact/authentic."
    else
      echo "   expected: $EXPECTED"
      echo "   RESULT: ❌ MISMATCH — do NOT trust this file. Re-download / ask MBZUAI."
      exit 2
    fi
  else
    echo "   (no expected hash given — ask MBZUAI for theirs and compare to the above)"
  fi

  # 2) Path-traversal ("zip-slip") safety ------------------------------------
  line; echo "2) Path-traversal / absolute-path check"
  if unzip -l "$TARGET" | awk '{print $4}' | grep -qE '(^/|\.\./)'; then
    echo "   RESULT: ❌ archive contains '..' or absolute paths — UNSAFE. Stop here."
    exit 3
  else
    echo "   RESULT: ✅ no unsafe paths."
  fi

  # 3) Contents listing -------------------------------------------------------
  line; echo "3) Contents (not extracted yet)"
  unzip -l "$TARGET" | sed 's/^/   /'

  # 4) Extract into a local sandbox folder ------------------------------------
  line; echo "4) Extracting into ./$SANDBOX/ (stays inside this folder)"
  rm -rf "$SANDBOX"; mkdir -p "$SANDBOX"
  unzip -q "$TARGET" -d "$SANDBOX"
  echo "   done."
else
  line; echo "1-4) Skipped — target is an already-extracted FOLDER"
  echo "   (macOS auto-unzipped it; that is normal and harmless — nothing ran)."
  echo "   NOTE: checksum & tamper checks need the ORIGINAL .zip. If you still"
  echo "   have it (check ~/Downloads or the Trash), running this on the .zip"
  echo "   gives the fuller check. Otherwise, scanning the folder is fine."
  line; echo "Contents:"
  find "$SANDBOX" -maxdepth 2 | sed 's/^/   /' | head -60
fi

# 5) Inventory: scripts, executables, Docker files ---------------------------
line; echo "5) What's inside"
echo "   Dockerfiles / compose:"
find "$SANDBOX" \( -iname 'Dockerfile*' -o -iname 'docker-compose*' \) | sed 's/^/     /' || true
echo "   Scripts (.sh .py):"
find "$SANDBOX" \( -iname '*.sh' -o -iname '*.py' \) | sed 's/^/     /' | head -40 || true
echo "   Executables / binaries (.exe .bin .app + +x files):"
{ find "$SANDBOX" \( -iname '*.exe' -o -iname '*.bin' -o -iname '*.app' \);
  find "$SANDBOX" -type f -perm -u+x; } | sort -u | sed 's/^/     /' | head -40 || true

# 6) Look for anything that phones home / moves data out ---------------------
line; echo "6) Network / telemetry / data-egress mentions (review these)"
if grep -rnIE "curl|wget|requests|urllib|http://|https://|socket|upload|s3://|boto|api\.|token|telemetry|analytics" "$SANDBOX" >/tmp/_netscan 2>/dev/null; then
  sed 's/^/   /' /tmp/_netscan | head -60
  echo "   (... review the lines above — not automatically bad, but understand"
  echo "    every outbound call before trusting the tool.)"
else
  echo "   RESULT: ✅ no obvious network/egress strings in text files."
fi
rm -f /tmp/_netscan

# 7) Show the README + Dockerfile so you can read them -----------------------
line; echo "7) README (first 60 lines, if present)"
find "$SANDBOX" -iname 'README*' | head -1 | while read -r f; do echo "   [$f]"; sed -n '1,60p' "$f" | sed 's/^/   /'; done
echo ""
echo "   Dockerfile (if present):"
find "$SANDBOX" -iname 'Dockerfile*' | head -1 | while read -r f; do echo "   [$f]"; sed -n '1,60p' "$f" | sed 's/^/   /'; done

# 8) Antivirus signature scan (ClamAV) --------------------------------------
line; echo "8) Antivirus scan (ClamAV signatures)"
SCANDIR="$(cd "$SANDBOX" && pwd)"
if command -v clamscan >/dev/null 2>&1; then
  echo "   using locally-installed clamscan; updating signatures (best-effort)..."
  freshclam >/dev/null 2>&1 || true
  clamscan -ri "$SCANDIR" || true
elif command -v docker >/dev/null 2>&1; then
  echo "   no local clamscan — running ClamAV via its official Docker image."
  echo "   (downloads the SCANNER itself, ~300 MB, not anything from the tool;"
  echo "    --platform linux/amd64 lets it run on Apple Silicon via emulation.)"
  docker run --rm --platform linux/amd64 -v "$SCANDIR:/scan:ro" \
      clamav/clamav clamscan -ri /scan || true
else
  echo "   ⚠️  SKIPPED — no antivirus available on this machine yet."
  echo "   Easiest fix: install Docker Desktop (free), then just re-run this script"
  echo "   and the AV scan happens automatically. Or run it yourself:"
  echo "     docker run --rm --platform linux/amd64 -v \"$SCANDIR:/scan:ro\" clamav/clamav clamscan -ri /scan"
fi

# Summary ---------------------------------------------------------------------
line
echo "DONE — nothing from the tool was executed."
echo ""
echo "NEXT STEPS (only after you/Claude have read sections 6, 7 and the AV result):"
echo "  • If it ships a Dockerfile, build and TEST RUN it air-gapped on dummy data:"
echo "      docker build -t mindsets \"$SANDBOX\""
echo "      docker run --rm --network none -v \"\$PWD/DUMMY:/data:ro\" -v \"\$PWD/out:/out\" mindsets"
echo "  • Never point it at real patient data until it's cleared AND Prof. Viana"
echo "    has approved local testing."
echo "======================================================================"
