#!/usr/bin/env bash
# =============================================================================
# scan_download.sh  —  SAFE, read-only check of a downloaded tool archive.
#
# It ONLY inspects the zip. It NEVER runs the tool inside it, and it never
# touches anything outside the folder you run it in. Use it to vet a
# third-party download (e.g. the MINDSETS zip) before trusting it.
#
# USAGE:
#   ./scan_download.sh  MINDSETS.zip  [expected_sha256]
#
# The optional second argument is the SHA-256 the MBZUAI team gave you; if you
# pass it, the script confirms the download is intact and authentic.
# =============================================================================
set -euo pipefail

ZIP="${1:-}"
EXPECTED="${2:-}"
SANDBOX="extracted"

line() { printf '%s\n' "----------------------------------------------------------------------"; }

if [[ -z "$ZIP" || ! -f "$ZIP" ]]; then
  echo "Usage: ./scan_download.sh  path/to/file.zip  [expected_sha256]"
  exit 1
fi

echo "======================================================================"
echo " SAFE DOWNLOAD CHECK (read-only — does NOT run the tool)"
echo " file: $ZIP"
echo " when: $(date)"
echo "======================================================================"

# 1) Checksum -----------------------------------------------------------------
line; echo "1) SHA-256 checksum"
GOT="$(shasum -a 256 "$ZIP" | awk '{print $1}')"
echo "   computed: $GOT"
if [[ -n "$EXPECTED" ]]; then
  if [[ "$GOT" == "$EXPECTED" ]]; then
    echo "   expected: $EXPECTED"
    echo "   RESULT  : ✅ MATCH — download is intact/authentic."
  else
    echo "   expected: $EXPECTED"
    echo "   RESULT  : ❌ MISMATCH — do NOT trust this file. Re-download / ask MBZUAI."
    exit 2
  fi
else
  echo "   (no expected hash given — ask MBZUAI for theirs and compare to the above)"
fi

# 2) Path-traversal ("zip-slip") safety --------------------------------------
line; echo "2) Path-traversal / absolute-path check"
if unzip -l "$ZIP" | awk '{print $4}' | grep -qE '(^/|\.\./)'; then
  echo "   RESULT: ❌ archive contains '..' or absolute paths — UNSAFE. Stop here."
  exit 3
else
  echo "   RESULT: ✅ no unsafe paths."
fi

# 3) Contents listing ---------------------------------------------------------
line; echo "3) Contents (not extracted yet)"
unzip -l "$ZIP" | sed 's/^/   /'

# 4) Extract into a local sandbox folder -------------------------------------
line; echo "4) Extracting into ./$SANDBOX/ (stays inside this folder)"
rm -rf "$SANDBOX"; mkdir -p "$SANDBOX"
unzip -q "$ZIP" -d "$SANDBOX"
echo "   done."

# 5) Inventory: scripts, executables, Docker files ---------------------------
line; echo "5) What's inside"
echo "   Dockerfiles / compose:"
find "$SANDBOX" -iname 'Dockerfile*' -o -iname 'docker-compose*' | sed 's/^/     /' || true
echo "   Scripts (.sh .py):"
find "$SANDBOX" \( -iname '*.sh' -o -iname '*.py' \) | sed 's/^/     /' | head -40 || true
echo "   Executables / binaries (.exe .bin .app + +x files):"
{ find "$SANDBOX" \( -iname '*.exe' -o -iname '*.bin' -o -iname '*.app' \);
  find "$SANDBOX" -type f -perm -u+x; } | sort -u | sed 's/^/     /' | head -40 || true

# 6) Look for anything that phones home / moves data out ---------------------
line; echo "6) Network / telemetry / data-egress mentions (review these)"
if grep -rnIE "curl|wget|requests|urllib|http://|https://|socket|upload|s3://|boto|api\.|token|telemetry|analytics" "$SANDBOX" >/tmp/_netscan 2>/dev/null; then
  sed 's/^/   /' /tmp/_netscan | head -60
  echo "   (... review the lines above — they are not automatically bad, but you"
  echo "    want to understand every outbound call before trusting the tool.)"
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

# Summary ---------------------------------------------------------------------
line
echo "DONE — nothing from the tool was executed."
echo ""
echo "NEXT STEPS (only after you/Claude have read sections 6 and 7):"
echo "  • If it ships a Dockerfile, build and TEST RUN it air-gapped on dummy data:"
echo "      docker build -t mindsets ./$SANDBOX"
echo "      docker run --rm --network none -v \"\$PWD/DUMMY:/data:ro\" -v \"\$PWD/out:/out\" mindsets"
echo "  • Never point it at real patient data until it's cleared AND Prof. Viana"
echo "    has approved local testing."
echo "======================================================================"
