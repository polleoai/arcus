#!/usr/bin/env bash
# Review-round check: full test suite + the URL-guard security regressions.
# Writes exactly ONE verdict line to $VERDICT_FILE (and stdout):
#   PASS n/n            — every collected test passed (skips listed separately)
#   FAIL p/n: <names>   — first failing test ids
# Full log goes to $LOG_FILE. Exit 0 on PASS, 1 on FAIL. A suite that fails to
# run at all is a FAIL, never a pass.
#
# Tests run against THIS checkout's source (PYTHONPATH), and the run FAILS if
# the imported package resolves anywhere else. Run it in a clean checkout at
# the commit under test; ARCUS_PY may name an existing interpreter with the
# dependencies (default: `uv run --frozen python`).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${ROUND_CHECK_DIR:-$ROOT/.round-check}"
mkdir -p "$OUT_DIR"
VERDICT_FILE="${VERDICT_FILE:-$OUT_DIR/verdict.txt}"
LOG_FILE="${LOG_FILE:-$OUT_DIR/round-check.log}"
JUNIT="$OUT_DIR/junit.xml"
rm -f "$JUNIT"

cd "$ROOT"
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"
git diff --quiet HEAD 2>/dev/null && [ -z "$(git status --porcelain --untracked-files=no)" ] || SHA="$SHA+dirty"
read -r -a PY <<<"${ARCUS_PY:-uv run --frozen python}"
export PYTHONPATH="$ROOT/packages/provider-runtime/src${PYTHONPATH:+:$PYTHONPATH}"
IMPORTED="$("${PY[@]}" -c 'import arcus.provider_runtime as m; print(m.__file__)' 2>&1)"
case "$IMPORTED" in
  "$ROOT"/*) ;;
  *) echo "FAIL 0/0 @$SHA: package imported from outside this checkout: $IMPORTED" | tee "$VERDICT_FILE"
     echo "log: $LOG_FILE"; exit 1 ;;
esac
{
  echo "== round-check $(date -u +%FT%TZ) @ $SHA"
  echo "== package: $IMPORTED"
  # The security regressions live under tests/ (collected by the full run);
  # name them explicitly too so a path/collection change can't drop them.
  "${PY[@]}" -m pytest -q -rfEs -p no:cacheprovider --junitxml="$JUNIT" \
    packages/provider-runtime/tests \
    packages/provider-runtime/tests/security/test_guard_regressions.py
  echo "== pytest exit: $?"
} >"$LOG_FILE" 2>&1

verdict="$(python3 - "$JUNIT" "$SHA" <<'PY'
import sys, xml.etree.ElementTree as ET
at = f"@{sys.argv[2]}"
try:
    root = ET.parse(sys.argv[1]).getroot()
except Exception as e:
    print(f"FAIL 0/0 {at}: suite did not run ({type(e).__name__})"); sys.exit()
cases = root.iter("testcase")
total = ran = 0; skipped = 0; bad = []; sec = 0
for c in cases:
    total += 1
    if "security" in (c.get("classname") or ""):
        sec += 1
    kids = {k.tag for k in c}
    if "skipped" in kids:
        skipped += 1; continue
    ran += 1
    if kids & {"failure", "error"}:
        bad.append(f"{c.get('classname','').split('.')[-1]}::{c.get('name')}")
if total == 0:
    print(f"FAIL 0/0 {at}: no tests collected")
elif sec == 0:
    print(f"FAIL {ran-len(bad)}/{ran} {at}: security regressions not collected")
elif bad:
    print(f"FAIL {ran-len(bad)}/{ran} {at}: " + ", ".join(bad[:5]) + (" ..." if len(bad) > 5 else ""))
else:
    print(f"PASS {ran}/{ran} {at}" + (f" ({skipped} skipped)" if skipped else ""))
PY
)"
echo "$verdict" | tee "$VERDICT_FILE"
echo "log: $LOG_FILE"
[[ "$verdict" == PASS* ]]
