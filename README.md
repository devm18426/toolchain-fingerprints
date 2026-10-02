# Cross-toolchain ABI fingerprints

Will a binary built by toolchain X run on target Y? This project answers that
without deploying to the device.

Every cross toolchain bakes a fixed set of runtime requirements into its
binaries: loader path, libc sonames, ISA, endianness, float ABI, time64
syscalls, minimum kernel and glibc symbol versions. This repo records those facts
for each toolchain. [toolchain-fingerprints-web](https://github.com/devm18426/toolchain-fingerprints-web)
matches them against what your target provides.

## Get the data

- Latest dataset: `https://github.com/devm18426/toolchain-fingerprints/releases/latest/download/fingerprints.json`
- Each release also ships the JSON schema and `SHA256SUMS`.

Within schema 2.x, fields are only ever added. Ignore fields you don't know, and
treat enum values you don't know as unknown.

## Use a toolchain image

Every fingerprinted toolchain is published as an image, with `make`, `patchelf`,
`patch`, `xz`, `bzip2` and `pkg-config` included:

```sh
docker run --rm -v "$PWD:/work" ghcr.io/devm18426/toolchain-fingerprints:mips32-uclibc-2017.11 make
```

`:<id>` is the current image; `:<id>-<hash>` pins an exact one. Every input is
pinned by digest or checksum, so an image never changes. Verify where an image came from:

```sh
gh attestation verify oci://ghcr.io/devm18426/toolchain-fingerprints:mips32-uclibc-2017.11 -R devm18426/toolchain-fingerprints
```

## Add a toolchain

Open an **Add toolchain** issue with an id and a tarball URL. CI turns it into a
pull request, builds the image and records its fingerprint.

To add a whole Bootlin release, run **Actions → import-bootlin → Run workflow**
with the release (e.g. `2026.08-1`), optionally filtered by arch, libc or channel.
It opens one pull request for the lot.

To do it locally instead (needs Docker and Python 3):

```sh
python generator/gen.py new <id> <tarball-url>
python generator/gen.py build <id>
python generator/gen.py probe <id>
python generator/gen.py merge
```

Then open a pull request with `toolchains/<id>/` and `data/`.

Some toolchains nobody distributes any more, such as TILE-Gx and TILEPro, are
compiled from pinned upstream sources inside their own Dockerfile
(`gen.py new-from-source`). The image is still built once and reused.
