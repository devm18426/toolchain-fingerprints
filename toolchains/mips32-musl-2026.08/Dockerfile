# Bootlin mips32 (big-endian, r1) musl, stable 2026.08-1
# Every input is pinned: base images by digest, the tarball by sha256.
# RUN steps are built with --network=none, so nothing unpinned can be fetched.

FROM alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8 AS fetch
ADD --checksum=sha256:0190387211125195f71b8397d02ccaaa15c6eb4ecbb94533d8a904065c0cc410 \
    https://toolchains.bootlin.com/downloads/releases/toolchains/mips32/tarballs/mips32--musl--stable-2026.08-1.tar.xz /tc.tar
RUN mkdir /tc && tar -xf /tc.tar -C /tc --strip-components=1

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
COPY --from=fetch /tc /opt/tc
ENV TC_ID=mips32-musl-2026.08 \
    CC=/opt/tc/bin/mips-buildroot-linux-musl-gcc \
    PATH=/opt/tc/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /work
