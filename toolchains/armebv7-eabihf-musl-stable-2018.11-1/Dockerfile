# Bootlin armebv7-eabihf--musl--stable-2018.11-1.tar.bz2
# Every input is pinned: base images by digest, downloads by sha256.
# RUN steps are built with --network=none, so nothing unpinned can be fetched.

FROM alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8 AS fetch
ADD --checksum=sha256:67198375a29fff14289effa41840ab6c9a519cde1863d5b494c3d6ee11ee22e1 \
    https://toolchains.bootlin.com/downloads/releases/toolchains/armebv7-eabihf/tarballs/armebv7-eabihf--musl--stable-2018.11-1.tar.bz2 /tc.tar
RUN mkdir /tc && tar -xjf /tc.tar -C /tc --strip-components=1

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
COPY --from=fetch /tc /opt/tc
# host binaries may carry an absolute RPATH into the prefix they were built in
RUN ln -s /opt/tc /opt/armebv7-eabihf--musl--stable-2018.11-1
ENV TC_ID=armebv7-eabihf-musl-stable-2018.11-1 \
    CC=/opt/tc/bin/armeb-buildroot-linux-musleabihf-gcc \
    PATH=/opt/tc/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /work
LABEL org.opencontainers.image.title="armebv7-eabihf-musl-stable-2018.11-1" \
      io.tcfp.tc_id="armebv7-eabihf-musl-stable-2018.11-1" \
      io.tcfp.toolchain.url="https://toolchains.bootlin.com/downloads/releases/toolchains/armebv7-eabihf/tarballs/armebv7-eabihf--musl--stable-2018.11-1.tar.bz2" \
      io.tcfp.toolchain.sha256="67198375a29fff14289effa41840ab6c9a519cde1863d5b494c3d6ee11ee22e1"
