#!/usr/bin/env bash
# fingerprint-dir.sh — fingerprint every toolchain found under a directory.
#
# A "toolchain" is any extracted cross toolchain that exposes a non-versioned
# <triple>-gcc (typically <dir>/bin/<triple>-gcc). Runs probe-toolchain.sh on
# each and emits a JSON array to stdout.
#
# Usage:  ./fingerprint-dir.sh [ROOT]        (default ROOT: ./toolchains)
set -u
ROOT="${1:-./toolchains}"
here="$(cd "$(dirname "$0")" && pwd)"

recs=()
while IFS= read -r gcc; do
  "$gcc" -dumpmachine >/dev/null 2>&1 || continue
  # tc_id = the toolchain's top dir under ROOT (fallback: triple)
  rel="${gcc#$ROOT/}"; id="${rel%%/*}"
  rec="$(TC_ID="$id" bash "$here/probe-toolchain.sh" "$gcc" 2>/dev/null)"
  case "$rec" in '{"'*) recs+=("$rec") ;; esac
done < <(find "$ROOT" -type f -name '*-gcc' 2>/dev/null | grep -vE -- '-gcc-[0-9]' | sort)

printf '[\n'
for i in "${!recs[@]}"; do
  [ "$i" -gt 0 ] && printf ',\n'
  printf '  %s' "${recs[$i]}"
done
printf '\n]\n'

# progress to stderr so stdout stays pure JSON
echo "fingerprinted ${#recs[@]} toolchain(s) under $ROOT" >&2
