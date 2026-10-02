#!/bin/bash
# build-cross.sh: bootstrap a GCC + glibc Linux cross toolchain from source.
#
# gen.py new-from-source inlines this script into a toolchain's Dockerfile, so
# the Dockerfile hash covers it. /src holds one tarball each of binutils, gcc,
# glibc, linux, gmp, mpfr and mpc, all pinned by sha256. gmp/mpfr/mpc are built
# inside gcc and linked statically, so the toolchain needs nothing from the build
# host at run time. Installs to /opt/tc, the path toolchain images use.
#
# Classic three-stage bootstrap: binutils, kernel headers, a bare C compiler,
# glibc headers + startfiles, a C compiler with shared libgcc, full glibc,
# then the final C/C++ compiler. Each step logs to a file; only step names are
# printed, and the end of the log if a step fails (build logs are size-capped).
set -euo pipefail
: "${TARGET:?}" "${LINUX_ARCH:?}"
P=/opt/tc
S=$P/$TARGET/sysroot
J=$(nproc)
export PATH=$P/bin:$PATH
mkdir -p /w/logs && cd /w

step(){                     # step <name> <command...>: run quietly, show the log tail on failure
  local name=$1; shift
  printf '===== %s\n' "$name"
  local t=$SECONDS rc
  # A subshell keeps each step's cd local; errexit must be set inside it and the
  # status read afterwards, because bash ignores set -e inside an if/|| condition.
  set +e
  ( set -e; cd /w; "$@" ) > "/w/logs/$name.log" 2>&1
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    printf '===== FAILED: %s (exit %s), last lines of its log:\n' "$name" "$rc"
    tail -n 100 "/w/logs/$name.log"
    exit 1
  fi
  printf '      done in %ss\n' $((SECONDS - t))
}

step unpack sh -c 'for f in /src/*.tar.*; do tar -xf "$f"; done'
BINUTILS=$(echo /w/binutils-*/) GCC=$(echo /w/gcc-*/) GLIBC=$(echo /w/glibc-*/) LINUX=$(echo /w/linux-*/)
for lib in gmp mpfr mpc; do mv /w/$lib-*/ "$GCC/$lib"; done      # in-tree: linked statically
COMMON=(--target="$TARGET" --prefix="$P" --with-sysroot="$S" --disable-nls --disable-multilib)
GCC_OFF=(--disable-libssp --disable-libgomp --disable-libquadmath --disable-libatomic
         --disable-libsanitizer --disable-libitm --disable-libvtv --disable-libcilkrts --disable-libmpx)
GLIBC_CONF=(--host="$TARGET" --build="$("$GLIBC"/scripts/config.guess)" --prefix=/usr
            --with-headers="$S/usr/include" --disable-werror --enable-kernel=3.2
            libc_cv_forced_unwind=yes libc_cv_c_cleanup=yes)

binutils(){
  mkdir b-binutils && cd b-binutils
  "$BINUTILS"/configure "${COMMON[@]}" --disable-werror
  make -j"$J" && make install
}
headers(){ make -C "$LINUX" ARCH="$LINUX_ARCH" INSTALL_HDR_PATH="$S/usr" headers_install; }
gcc1(){
  mkdir b-gcc1 && cd b-gcc1
  "$GCC"/configure "${COMMON[@]}" "${GCC_OFF[@]}" --with-newlib --without-headers \
    --disable-shared --disable-threads --enable-languages=c
  make -j"$J" all-gcc all-target-libgcc && make install-gcc install-target-libgcc
}
glibc_start(){
  mkdir b-glibc1 && cd b-glibc1
  "$GLIBC"/configure "${GLIBC_CONF[@]}"
  make install-bootstrap-headers=yes install-headers install_root="$S"
  make -j"$J" csu/subdir_lib
  # gcc's OS directory is relative to lib/: "." is usr/lib, "../lib64" is usr/lib64
  local libdir; libdir=$(realpath -m "$S/usr/lib/$("$TARGET"-gcc -print-multi-os-directory)")
  mkdir -p "$libdir"
  cp csu/crt1.o csu/crti.o csu/crtn.o "$libdir"/
  "$TARGET"-gcc -nostdlib -nostartfiles -shared -x c /dev/null -o "$libdir/libc.so"
  touch "$S/usr/include/gnu/stubs.h"
}
gcc2(){
  mkdir b-gcc2 && cd b-gcc2
  "$GCC"/configure "${COMMON[@]}" "${GCC_OFF[@]}" --enable-shared --disable-threads --enable-languages=c
  make -j"$J" all-gcc all-target-libgcc && make install-gcc install-target-libgcc
}
glibc_full(){
  rm -rf b-glibc1 && mkdir b-glibc && cd b-glibc
  "$GLIBC"/configure "${GLIBC_CONF[@]}"
  make -j"$J" && make install install_root="$S"
}
gcc3(){
  mkdir b-gcc3 && cd b-gcc3
  "$GCC"/configure "${COMMON[@]}" --disable-libsanitizer --disable-libvtv --disable-libcilkrts --disable-libmpx \
    --enable-shared --enable-threads=posix --enable-languages=c,c++
  make -j"$J" && make install
}
smoke(){
  printf '#include <stdio.h>\nint main(void){puts("hi");return 0;}\n' > hello.c
  "$TARGET"-gcc -o hello hello.c && "$TARGET"-gcc -static -o hello-static hello.c
  printf '#include <stdexcept>\nint main(){try{throw std::runtime_error("x");}catch(...){}}\n' > hello.cc
  "$TARGET"-g++ -o hellocc hello.cc
}

step binutils binutils
step linux-headers headers
step gcc-stage1 gcc1
step glibc-headers-startfiles glibc_start
step gcc-stage2 gcc2
step glibc glibc_full
step gcc-final gcc3
step smoke-test smoke
"$TARGET"-readelf -h -l /w/hello | grep -E "Class|Data|Machine|interpreter"
cd / && rm -rf /w
