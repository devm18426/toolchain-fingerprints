# TILEPro (32-bit), glibc 2.26 (the last with TILEPro), GCC 7.5.0, binutils 2.30, Linux 4.16 headers
# Built from source: nobody distributes this toolchain any more. Every source
# tarball is pinned by sha256, the build host is a pinned Debian image with apt
# from a dated snapshot, and the build script is inlined below, so this file's
# hash covers everything that goes into the toolchain.

FROM debian:buster-slim@sha256:bb3dc79fddbca7e8903248ab916bb775c96ec61014b3d02b4f06043b604726dc AS build
RUN printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20240701T000000Z buster main\ndeb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20240701T000000Z buster-updates main\ndeb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20240701T000000Z buster/updates main\n' > /etc/apt/sources.list \
 && apt-get -o Acquire::Check-Valid-Until=false update \
 && apt-get install -y --no-install-recommends build-essential bison flex gawk texinfo file m4 \
      xz-utils bzip2 perl python3 \
 && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:6e46b8aeae2f727a36f0bd9505e405768a72218f1796f0d09757d45209871ae6 \
    https://ftp.gnu.org/gnu/binutils/binutils-2.30.tar.xz /src/
ADD --checksum=sha256:b81946e7f01f90528a1f7352ab08cc602b9ccc05d4e44da4bd501c5a189ee661 \
    https://ftp.gnu.org/gnu/gcc/gcc-7.5.0/gcc-7.5.0.tar.xz /src/
ADD --checksum=sha256:41026d713ba4f7a5e9d514b876ce4ed28a1d993c0c58b42b2a2597d6a0e83021 \
    https://cdn.kernel.org/pub/linux/kernel/v4.x/linux-4.16.18.tar.xz /src/
ADD --checksum=sha256:498449a994efeba527885c10405993427995d3f86b8768d8cdf8d9dd7c6b73e8 \
    https://gcc.gnu.org/pub/gcc/infrastructure/gmp-6.1.0.tar.bz2 /src/
ADD --checksum=sha256:d3103a80cdad2407ed581f3618c4bed04e0c92d1cf771a65ead662cc397f7775 \
    https://gcc.gnu.org/pub/gcc/infrastructure/mpfr-3.1.4.tar.bz2 /src/
ADD --checksum=sha256:617decc6ea09889fb08ede330917a00b16809b8db88c29c31bfbb49cbf88ecc3 \
    https://gcc.gnu.org/pub/gcc/infrastructure/mpc-1.0.3.tar.gz /src/
ADD --checksum=sha256:e54e0a934cd2bc94429be79da5e9385898d2306b9eaf3c92d5a77af96190f6bd \
    https://ftp.gnu.org/gnu/glibc/glibc-2.26.tar.xz /src/
ENV TARGET=tilepro-linux-gnu LINUX_ARCH=tile
RUN <<'BUILD'
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
  # Placeholder libc.so for the next GCC stage; glibc_full replaces it. It defines the libc
  # functions libgcc's unwinder uses, because IA-64 links its libunwind.so with -z defs.
  printf 'void %s(void){}\n' malloc free memcpy memset abort dl_iterate_phdr > stub.c
  "$TARGET"-gcc -nostdlib -nostartfiles -shared -fno-builtin -w stub.c -o "$libdir/libc.so"
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
BUILD

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
# build tools (make, patchelf, patch, xz, bzip2, pkg-config), pinned .debs
ADD --checksum=sha256:a1a83af8cbd854af887b72ad196b1f4af58387815e21ced1000253a116a46e2a \
    https://snapshot.debian.org/archive/debian/20260918T000000Z/pool/main/m/make-dfsg/make_4.3-4.1_amd64.deb /debs/
ADD --checksum=sha256:0364b90e81faabbb9569076063952c7bcbf07f83d954a2f2602a163d65be60e6 \
    https://snapshot.debian.org/archive/debian/20260918T000000Z/pool/main/p/patchelf/patchelf_0.14.3-1+b1_amd64.deb /debs/
ADD --checksum=sha256:8c6d49b771530dbe26d7bd060582dc7d2b4eeb603a20789debc1ef4bbbc4ef67 \
    https://snapshot.debian.org/archive/debian/20260918T000000Z/pool/main/p/patch/patch_2.7.6-7_amd64.deb /debs/
ADD --checksum=sha256:f99fc3fe4b4e5baecf5fd8b53853a82e1e71cb5ec187b36b0ddcf227a8961c8f \
    https://snapshot.debian.org/archive/debian-security/20260918T000000Z/pool/updates/main/x/xz-utils/xz-utils_5.4.1-1+deb12u2_amd64.deb /debs/
ADD --checksum=sha256:438871b3f5c5c7a357a9840951dab9dab8db7eb1ff760a563226fafa111b99e5 \
    https://snapshot.debian.org/archive/debian/20260918T000000Z/pool/main/b/bzip2/bzip2_1.0.8-5+b1_amd64.deb /debs/
ADD --checksum=sha256:da01fb901123ae498c36387a32240e09e1f2866810146c5a574273f7eaf31093 \
    https://snapshot.debian.org/archive/debian/20260918T000000Z/pool/main/p/pkgconf/libpkgconf3_1.8.1-1_amd64.deb /debs/
ADD --checksum=sha256:8fb5a8f83e46ad04b4cf02651ceec56c0611a335cf0d30780d859a95d0400174 \
    https://snapshot.debian.org/archive/debian/20260918T000000Z/pool/main/p/pkgconf/pkgconf-bin_1.8.1-1_amd64.deb /debs/
ADD --checksum=sha256:4e3ce982b5fedc6c6119268435504a64f5ffcc6d93aaecaea902d816eba1215f \
    https://snapshot.debian.org/archive/debian/20260918T000000Z/pool/main/p/pkgconf/pkgconf_1.8.1-1_amd64.deb /debs/
ADD --checksum=sha256:312b2bdeff4671f8e0d589c124554890e944dd083061e9ad6f129bc76a970765 \
    https://snapshot.debian.org/archive/debian/20260918T000000Z/pool/main/p/pkgconf/pkg-config_1.8.1-1_amd64.deb /debs/
RUN dpkg -i /debs/*.deb && rm -rf /debs
COPY --from=build /opt/tc /opt/tc
ENV TC_ID=tilepro-glibc-2.26 \
    CC=/opt/tc/bin/tilepro-linux-gnu-gcc \
    PATH=/opt/tc/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /work
LABEL org.opencontainers.image.title="tilepro-glibc-2.26" \
      io.tcfp.tc_id="tilepro-glibc-2.26" \
      io.tcfp.toolchain.url="https://cdn.kernel.org/pub/linux/kernel/v4.x/linux-4.16.18.tar.xz https://ftp.gnu.org/gnu/binutils/binutils-2.30.tar.xz https://ftp.gnu.org/gnu/gcc/gcc-7.5.0/gcc-7.5.0.tar.xz https://ftp.gnu.org/gnu/glibc/glibc-2.26.tar.xz https://gcc.gnu.org/pub/gcc/infrastructure/gmp-6.1.0.tar.bz2 https://gcc.gnu.org/pub/gcc/infrastructure/mpc-1.0.3.tar.gz https://gcc.gnu.org/pub/gcc/infrastructure/mpfr-3.1.4.tar.bz2" \
      io.tcfp.toolchain.sha256="afe1745053bb2d5e2c6e395de77bced98ca6e63afcec5be175b9ec6a6d6f8b97"
