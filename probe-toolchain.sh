#!/usr/bin/env bash
# probe-toolchain.sh — fingerprint one cross toolchain's runtime output ABI.
#
# Emits ONE compact JSON object describing what this toolchain's binaries depend
# on at runtime (loader/interp, libc soname, ISA, endianness, float ABI), so a
# target can be matched by inspecting its /lib instead of trial-and-error deploy.
#
# Usage:  probe-toolchain.sh [/path/to/<triple>-gcc]
#   No arg -> autodetect a non-versioned *-gcc under $TCBIN, /opt/*/bin, PATH.
#   TC_ID=<label> tags the record (default: the target triple).
set -u

CC="${1:-}"
if [ -z "$CC" ]; then
  for d in ${TCBIN:-} /opt/tc/bin /opt/*/bin /usr/bin; do
    for g in "$d"/*-gcc; do
      case "$g" in *-gcc) [ -x "$g" ] && { CC="$g"; break 2; };; esac
    done
  done
fi
[ -n "$CC" ] && { command -v "$CC" >/dev/null 2>&1 || [ -x "$CC" ]; } || { echo '{"error":"no cross gcc found"}'; exit 1; }

pfx="${CC%gcc}"
RE="${pfx}readelf"; { command -v "$RE" >/dev/null 2>&1 || [ -x "$RE" ]; } || RE=readelf

triple="$("$CC" -dumpmachine 2>/dev/null)"
gccver="$("$CC" -dumpversion 2>/dev/null)"
sysroot="$("$CC" -print-sysroot 2>/dev/null)"

tmp="$(mktemp -d)"; printf 'int main(void){return 0;}\n' > "$tmp/h.c"
"$CC" -o "$tmp/dyn" "$tmp/h.c" 2>/dev/null && dyn_ok=true || dyn_ok=false
"$CC" -static -o "$tmp/sta" "$tmp/h.c" 2>/dev/null && static_ok=true || static_ok=false

hdr="$("$RE" -h "$tmp/dyn" 2>/dev/null)"
elf_class="$(printf '%s\n' "$hdr" | sed -n 's/.*Class: *//p' | head -1)"
endian="$(printf '%s\n' "$hdr" | sed -n 's/.*Data: *2.s complement, *//p' | head -1)"
machine="$(printf '%s\n' "$hdr" | sed -n 's/.*Machine: *//p' | head -1)"
etype="$(printf '%s\n' "$hdr" | sed -n 's/.*Type: *//p' | head -1 | awk '{print $1}')"
attrs="$("$RE" -A "$tmp/dyn" 2>/dev/null)"
isa="$(printf '%s\n' "$attrs" | sed -n 's/.*ISA: *//p' | head -1)"
float="$(printf '%s\n' "$hdr$attrs" | grep -oiE 'soft.?float|hard.?float|softfp' | head -1)"
interp="$("$RE" -l "$tmp/dyn" 2>/dev/null | grep -o '/[^]]*ld-[^]]*' | head -1)"
needed="$("$RE" -d "$tmp/dyn" 2>/dev/null | grep NEEDED | grep -o '\[.*\]' | tr -d '[]' | paste -sd, -)"

find_soname(){ [ -e "$1" ] || return; "$RE" -d "$1" 2>/dev/null | sed -n 's/.*SONAME.*\[\(.*\)\]/\1/p' | head -1; }
ldso=""; libc=""
if [ -n "$sysroot" ]; then
  for f in "$sysroot"/lib/ld-*.so* "$sysroot"/lib/*/ld-*.so*; do s="$(find_soname "$f")"; [ -n "$s" ] && { ldso="$s"; break; }; done
  for f in "$sysroot"/lib/libc.so* "$sysroot"/lib/libuClibc*.so "$sysroot"/lib/*/libc.so*; do s="$(find_soname "$f")"; [ -n "$s" ] && { libc="$s"; break; }; done
fi
case "$ldso$interp" in
  *musl*)   libc_kind=musl ;;
  *uClibc*) libc_kind=uclibc ;;
  *ld-linux*|*ld.so*) libc_kind=glibc ;;
  *)        libc_kind=unknown ;;
esac
# musl's loader IS libc; surface its soname from the interp basename
[ "$libc_kind" = musl ] && [ -z "$libc" ] && libc="$(basename "$interp")"

j(){ printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
printf '{"tc_id":"%s","triple":"%s","gcc":"%s","libc_kind":"%s","elf_class":"%s","endian":"%s","machine":"%s","isa":"%s","float":"%s","default_type":"%s","interp":"%s","needed":"%s","ldso_soname":"%s","libc_soname":"%s","dynamic_ok":%s,"static_ok":%s}\n' \
  "$(j "${TC_ID:-$triple}")" "$(j "$triple")" "$(j "$gccver")" "$(j "$libc_kind")" "$(j "$elf_class")" "$(j "$endian")" "$(j "$machine")" "$(j "$isa")" "$(j "${float:-}")" "$(j "$etype")" "$(j "$interp")" "$(j "$needed")" "$(j "$ldso")" "$(j "$libc")" "$dyn_ok" "$static_ok"
rm -rf "$tmp"
