# Alpha (64-bit), glibc 2.39, GCC 13.3.0, binutils 2.42, Linux 6.6 headers: no prebuilt glibc toolchain is distributed for it
# Built from source: nobody distributes this toolchain any more. Every source
# tarball is pinned by sha256, the build host is a pinned Debian image with apt
# from a dated snapshot, and the build script is inlined below, so this file's
# hash covers everything that goes into the toolchain.

FROM debian:buster-slim@sha256:bb3dc79fddbca7e8903248ab916bb775c96ec61014b3d02b4f06043b604726dc AS build
RUN printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20240701T000000Z buster main\ndeb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20240701T000000Z buster-updates main\ndeb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20240701T000000Z buster/updates main\n' > /etc/apt/sources.list \
 && apt-get -o Acquire::Check-Valid-Until=false update \
 && apt-get install -y --no-install-recommends build-essential bison flex gawk texinfo file m4 \
      xz-utils bzip2 perl python3 rsync \
 && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:f6e4d41fd5fc778b06b7891457b3620da5ecea1006c6a4a41ae998109f85a800 \
    https://ftp.gnu.org/gnu/binutils/binutils-2.42.tar.xz /src/
ADD --checksum=sha256:0845e9621c9543a13f484e94584a49ffc0129970e9914624235fc1d061a0c083 \
    https://ftp.gnu.org/gnu/gcc/gcc-13.3.0/gcc-13.3.0.tar.xz /src/
ADD --checksum=sha256:9eb33173b0e04e898d431587c78c47ee6e19e3fdf2ea007323de82af14bb2a4f \
    https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.6.158.tar.xz /src/
ADD --checksum=sha256:eae9326beb4158c386e39a356818031bd28f3124cf915f8c5b1dc4c7a36b4d7c \
    https://gcc.gnu.org/pub/gcc/infrastructure/gmp-6.2.1.tar.bz2 /src/
ADD --checksum=sha256:feced2d430dd5a97805fa289fed3fc8ff2b094c02d05287fd6133e7f1f0ec926 \
    https://gcc.gnu.org/pub/gcc/infrastructure/mpfr-4.1.0.tar.bz2 /src/
ADD --checksum=sha256:17503d2c395dfcf106b622dc142683c1199431d095367c6aacba6eec30340459 \
    https://gcc.gnu.org/pub/gcc/infrastructure/mpc-1.2.1.tar.gz /src/
ADD --checksum=sha256:f77bd47cf8170c57365ae7bf86696c118adb3b120d3259c64c502d3dc1e2d926 \
    https://ftp.gnu.org/gnu/glibc/glibc-2.39.tar.xz /src/
ENV TARGET=alpha-linux-gnu LINUX_ARCH=alpha libc_cv_cxx_link_ok=no
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
ENV TC_ID=alpha-glibc-2.39 \
    CC=/opt/tc/bin/alpha-linux-gnu-gcc \
    PATH=/opt/tc/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /work
LABEL org.opencontainers.image.title="alpha-glibc-2.39" \
      io.tcfp.tc_id="alpha-glibc-2.39" \
      io.tcfp.toolchain.url="https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.6.158.tar.xz https://ftp.gnu.org/gnu/binutils/binutils-2.42.tar.xz https://ftp.gnu.org/gnu/gcc/gcc-13.3.0/gcc-13.3.0.tar.xz https://ftp.gnu.org/gnu/glibc/glibc-2.39.tar.xz https://gcc.gnu.org/pub/gcc/infrastructure/gmp-6.2.1.tar.bz2 https://gcc.gnu.org/pub/gcc/infrastructure/mpc-1.2.1.tar.gz https://gcc.gnu.org/pub/gcc/infrastructure/mpfr-4.1.0.tar.bz2" \
      io.tcfp.toolchain.sha256="88f03af1ac6cdfc76aee37f029f8ffb49d71f656fccbbb7d13b6552fa481b55a"
