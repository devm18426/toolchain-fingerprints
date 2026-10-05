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
cap(){ local f="$1"; shift; "$@" > "$OUT/$f" 2>/dev/null; echo $? > "$OUT/$f.rc"; }  # cap <name> <cmd...>

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
  # lib64/ and lib/<multiarch>/ cover 64-bit and Debian-style sysroots
  put ldso_soname "$(first_soname "$sysroot"/lib/ld-*.so* "$sysroot"/lib/ld.so* "$sysroot"/lib/ld64.so*                                   "$sysroot"/lib64/ld-*.so* "$sysroot"/lib64/ld64.so* "$sysroot"/lib/*/ld-*.so*)"
  put libc_soname "$(first_soname "$sysroot"/lib/libc.so* "$sysroot"/lib/libuClibc*.so                                   "$sysroot"/lib64/libc.so* "$sysroot"/lib/*/libc.so*)"
fi

# --- time_t width -----------------------------------------------------------
for n in 4 8; do
  cap "time_t.$n" "$CC" -DTSZ=$n -c -o "$W/ts.o" "$here/corpus/timesize.c"
done

# --- probe corpus: real programs need more than hello ------------------------
CXX="${pfx}g++"; [ -x "$CXX" ] || CXX=""
put cxx "$CXX"
for src in "$here"/corpus/*.c "$here"/corpus/*.cc; do
  n="$(basename "${src%.*}")"; [ "$n" = timesize ] && continue
  case "$src" in *.cc) c="$CXX"; [ -n "$c" ] || continue ;; *) c="$CC" ;; esac
  libs="-lpthread -ldl -lm"; [ "$n" = cxx ] && libs=""
  cap "corpus.$n.link" "$c" -o "$W/$n" "$src" $libs
  if [ -f "$W/$n" ]; then
    cap "corpus.$n.d" "$RE" -d "$W/$n"
    cap "corpus.$n.V" "$RE" -V "$W/$n"
    cap "corpus.$n.syms" "$RE" -W --dyn-syms "$W/$n"
    cap "corpus.$n.n" "$RE" -n "$W/$n"
    cap "corpus.$n.A" "$RE" -A "$W/$n"
  fi
done

# --- compiler defaults ----------------------------------------------------------
# with an input to compile: wrappers that add -Wl,... give the driver a linker input,
# and with no source file to hand to cc1 it then prints nothing (and exits 0)
cap gcc_target "$CC" -Q --help=target -S -x c /dev/null -o /dev/null

# --- sysroot: headers, libc config, shipped sonames -----------------------------
if [ -n "$sysroot" ]; then
  inc="$sysroot/usr/include"
  cap linux_version_h cat "$inc/linux/version.h"
  cap uclibc_config grep -hE '^#(define|undef) __(UCLIBC_|LDSO_)' "$inc/bits/uClibc_config.h"
  cap features_h grep -hE '^#[[:space:]]*define[[:space:]]+__(GLIBC|GLIBC_MINOR|UCLIBC_MAJOR|UCLIBC_MINOR|UCLIBC_SUBLEVEL)__[[:space:]]' "$inc/features.h"
  for f in "$sysroot"/lib/libc.so.6 "$sysroot"/lib64/libc.so.6 "$sysroot"/lib/*/libc.so.6; do
    # only the version definitions (GLIBC_x.y names); the full -V dump is ~70 KB
    [ -e "$f" ] && { cap libc_V sh -c '"$1" -V "$2" | grep -oE "Name: GLIBC_[0-9.]+" | sort -u' _ "$RE" "$f"; break; }
  done
  : > "$OUT/sysroot_sonames"
  for f in "$sysroot"/lib/*.so* "$sysroot"/lib64/*.so* "$sysroot"/usr/lib/*.so* "$sysroot"/usr/lib64/*.so*; do
    [ -f "$f" ] && soname "$f" >> "$OUT/sysroot_sonames"
  done
fi
# Buildroot SDKs (Bootlin) list package versions; used where headers have none (musl)
tcroot="$(cd "$(dirname "$CC")/.." && pwd)"
[ -f "$tcroot/summary.csv" ] && cap summary grep -E '^"(musl|uclibc|glibc|linux-headers)",' "$tcroot/summary.csv"

tar -C "$OUT" -cf - .
rm -rf "$OUT" "$W"
