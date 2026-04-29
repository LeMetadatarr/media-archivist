#!/usr/bin/env bash
# Run every live check; report PASS/FAIL per check, exit non-zero if any fail.
#
# These hit real third-party endpoints — they may flake when the upstream
# changes its HTML or rate-limits us. Use as a smoke test, not a CI gate.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"

declare -a CHECKS=(
    "youtube"
    "bandcamp"
    "soundcloud"
    "internet_archive"
    "musicbrainz"
    "wikidata"
)

declare -a PASSED FAILED SKIPPED
EXIT=0

for name in "${CHECKS[@]}"; do
    script="$HERE/check_${name}.py"
    [[ -f "$script" ]] || continue
    echo
    echo "==> $name"
    set +e
    out=$(python "$script" 2>&1)
    code=$?
    set -e
    echo "$out"
    if [[ $code -eq 0 ]]; then
        if echo "$out" | grep -q "^SKIP"; then
            SKIPPED+=("$name")
        else
            PASSED+=("$name")
        fi
    else
        FAILED+=("$name")
        EXIT=1
    fi
done

echo
echo "==> server smoke"
if PORT=18099 bash "$HERE/run_server_smoke.sh" > /tmp/server_smoke.out 2>&1; then
    PASSED+=("server")
    echo "PASS: server"
else
    FAILED+=("server")
    EXIT=1
    echo "FAIL: server (see /tmp/server_smoke.out)"
fi

echo
echo "================================="
echo "PASSED:  ${PASSED[*]:-(none)}"
echo "SKIPPED: ${SKIPPED[*]:-(none)}"
echo "FAILED:  ${FAILED[*]:-(none)}"
echo "================================="
exit $EXIT
