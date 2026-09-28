#!/usr/bin/env bash
# probe.sh: runs INSIDE a toolchain image. Collects raw facts about what the
# toolchain's binaries need at runtime and writes them, one file per fact, as a
# tar stream on stdout. It does no interpretation: gen.py normalizes the raw
# files into a schema record, so parsing lives in one testable place.
#
# The image provides the toolchain; CC (and TC_ID) come from the image's ENV.
# If CC is unset, a non-versioned *-gcc under /opt/tc/bin is used.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
OUT="$(mktemp -d)"; W="$(mktemp -d)"

CC="${CC:-}"
if [ -z "$CC" ]; then
  for g in /opt/tc/bin/*-gcc; do [ -x "$g" ] && { CC="$g"; break; }; done
fi
if [ -z "$CC" ] || ! { command -v "$CC" >/dev/null 2>&1 || [ -x "$CC" ]; }; then
  echo "probe: no cross gcc found (set ENV CC in the Dockerfile)" >&2; exit 2
fi
CC="$(command -v "$CC")"
pfx="${CC%gcc}"
RE="${pfx}readelf"; [ -x "$RE" ] || RE="$(command -v readelf || true)"
[ -n "$RE" ] || { echo "probe: no readelf" >&2; exit 2; }

put(){ printf '%s\n' "$2" > "$OUT/$1"; }      # put <name> <value>
cap(){ n="$1"; shift; "$@" > "$OUT/$n" 2>/dev/null; echo $? > "$OUT/$n.rc"; }  # cap <name> <cmd...>

put tc_id "${TC_ID:-}"
put cc "$CC"
cap triple      "$CC" -dumpmachine
cap gcc_version "$CC" -dumpversion
cap sysroot     "$CC" -print-sysroot
sysroot="$(cat "$OUT/sysroot")"

# --- the baseline program, linked dynamically and statically --------------
printf 'int main(void){return 0;}\n' > "$W/h.c"
cap link_dyn    "$CC"         -o "$W/dyn" "$W/h.c"
cap link_static "$CC" -static -o "$W/sta" "$W/h.c"
if [ -f "$W/dyn" ]; then
  cap dyn.h "$RE" -h "$W/dyn"
  cap dyn.A "$RE" -A "$W/dyn"
  cap dyn.l "$RE" -l "$W/dyn"
  cap dyn.d "$RE" -d "$W/dyn"
fi

# --- sysroot loader and libc SONAMEs ----------------------------------------
soname(){ "$RE" -d "$1" 2>/dev/null | sed -n 's/.*(SONAME).*\[\(.*\)\]/\1/p' | head -1; }
first_soname(){ for f in "$@"; do [ -e "$f" ] || continue; s="$(soname "$f")"; [ -n "$s" ] && { echo "$s"; return; }; done; }
if [ -n "$sysroot" ]; then
  put ldso_soname "$(first_soname "$sysroot"/lib/ld-*.so* "$sysroot"/lib/*/ld-*.so*)"
  put libc_soname "$(first_soname "$sysroot"/lib/libc.so* "$sysroot"/lib/libuClibc*.so "$sysroot"/lib/*/libc.so*)"
fi

tar -C "$OUT" -cf - .
rm -rf "$OUT" "$W"
