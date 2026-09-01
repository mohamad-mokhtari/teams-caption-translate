#!/usr/bin/env bash
# Every test, in one command. Nothing here needs an API key or a network.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PY=server/.venv/bin/python
[ -x "$PY" ] || PY=python3
$PY -c "import quickjs" 2>/dev/null || { echo "need quickjs:  $PY -m pip install quickjs"; exit 1; }

fail=0
$PY tests/syntax_check.py extension/content.js || fail=1
$PY tests/syntax_check.py console-test.js      || fail=1

for t in test_smoke test_panel test_language test_transcript; do
  out=$($PY "tests/$t.py" 2>&1) || fail=1
  printf "%-18s %3d pass" "$t" "$(grep -c PASS <<<"$out")"
  n=$(grep -c FAIL <<<"$out" || true)
  [ "$n" -gt 0 ] && { printf "  %d FAIL\n" "$n"; grep FAIL <<<"$out"; } || printf "\n"
  grep -q Traceback <<<"$out" && { echo "$out" | tail -20; fail=1; }
done

rm -rf /tmp/mct-transcript-* /tmp/mct-lang-* 2>/dev/null
[ "$fail" = 0 ] && echo "all green" || echo "FAILURES"
exit "$fail"
