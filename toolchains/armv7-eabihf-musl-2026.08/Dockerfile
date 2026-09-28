# Bootlin armv7-eabihf (little-endian, hard-float) musl, stable 2026.08-1
# Every input is pinned: base images by digest, the tarball by sha256.
# RUN steps are built with --network=none, so nothing unpinned can be fetched.

FROM alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8 AS fetch
ADD --checksum=sha256:9147bafae4aa272321a3c6440d04d83b7e23411b2d344f875541d84c4444ba9b \
    https://toolchains.bootlin.com/downloads/releases/toolchains/armv7-eabihf/tarballs/armv7-eabihf--musl--stable-2026.08-1.tar.xz /tc.tar
RUN mkdir /tc && tar -xf /tc.tar -C /tc --strip-components=1

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
COPY --from=fetch /tc /opt/tc
ENV TC_ID=armv7-eabihf-musl-2026.08 \
    CC=/opt/tc/bin/arm-buildroot-linux-musleabihf-gcc \
    PATH=/opt/tc/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /work
