# Cross-toolchain ABI fingerprints

Answer one question without deploying to the device: *will a binary built by
toolchain X run on target Y?*

A cross toolchain's binaries carry a fixed set of runtime requirements: a
loader path, libc sonames, an ISA, endianness, a float ABI. This project
records those facts per toolchain. The page that compares them against a
target lives in [toolchain-fingerprints-web](https://github.com/devm18426/toolchain-fingerprints-web)
and consumes this repo's releases. See [docs/DESIGN.md](docs/DESIGN.md) for the
reasoning and [docs/SETUP.md](docs/SETUP.md) for the one-time GitHub setup.

## What this repo publishes

| what | where |
|---|---|
| the dataset | a GitHub Release per change, `data-<schema>.<n>`, with `fingerprints.json`, the schema and `SHA256SUMS`; latest at `releases/latest/download/fingerprints.json` |
| toolchain images | `ghcr.io/devm18426/toolchain-fingerprints:<tc_id>` (current) and `:<tc_id>-<dockerfile hash>` (pinned), with build provenance attestations |

## How a toolchain gets in

1. Open an **Add toolchain** issue with an id and a tarball URL (or run `gen.py new` locally and push a branch).
2. The bot runs `gen.py new` and opens a PR with the Dockerfile.
3. CI lints and tests, builds only the new image, pushes it to GHCR, probes it, and commits the record to the PR.
4. You merge once `ready` is green. A release is cut and the web repo picks it up.

Nothing runs on a schedule: a record is redone only when its Dockerfile or the probe changes.

## Layout

| path | component | role |
|---|---|---|
| `schema/fingerprint.schema.json` | contract | the only thing the two components share |
| `toolchains/<id>/Dockerfile` | generator | one fully pinned toolchain per directory |
| `generator/probe/` | generator | `probe.sh` and the corpus: what runs inside the image |
| `generator/normalize.py` | generator | turns raw probe output into a record; one small decoder per architecture |
| `generator/probe/corpus/` | generator | small C/C++ programs linked by the probe (pthreads, select/clock_gettime, dlopen, libm, 64-bit division, exceptions) |
| `generator/gen.py` | generator | lint, build, probe, merge, validate (stdlib Python only) |
| `generator/tests/` | generator | `python -m unittest discover -s generator/tests` (no Docker needed) |
| `data/fingerprints.json` | output | the dataset, validated against the schema |
| `data/raw/<id>.json` | output | raw probe output per toolchain; records are re-derived from it |
| `.github/workflows/` | CI | `ci.yml` (PR build/probe/record), `add-toolchain.yml` (issue bot), `release.yml` |

## Generating records

A pinned toolchain always produces the same binaries, so a toolchain is probed
once. Its raw probe output is kept in `data/raw/<id>.json`, and the record is
derived from that by `generator/normalize.py`. So:

| what changed | what happens | Docker? |
|---|---|---|
| the toolchain's Dockerfile, or `generator/probe/` | rebuild the image and re-probe | yes |
| `generator/normalize.py` (a fix, a new field, a new architecture) | `gen.py merge` re-derives records from `data/raw/` | no |

All of these inputs are hashed into the record's `provenance`. There is no
scheduled recheck.

One step per command:

```sh
python generator/gen.py status                    # what is up to date / stale
python generator/gen.py build mips32-musl-2026.08 # docker build (RUN steps offline)
python generator/gen.py probe mips32-musl-2026.08 # -> .cache/records/<id>.json
python generator/gen.py merge                     # -> data/fingerprints.json
python generator/gen.py validate                  # check against the schema
```

### Adding a toolchain

```sh
python generator/gen.py new arm-uclibc-2026.08 https://toolchains.bootlin.com/.../armv7-eabihf--uclibc--stable-2026.08-1.tar.xz
```

`new` downloads the tarball (kept in `.cache/downloads`), records its sha256,
picks the cross gcc from the tarball's `bin/` (the full-triple name; override
with `--cc`), and writes `toolchains/<id>/Dockerfile`. Then run `build`,
`probe` and `merge` as above and commit the Dockerfile with
`data/fingerprints.json`.

The generator refuses a hand-written Dockerfile (`gen.py lint`) unless every
input is pinned:

- every `FROM` is pinned by `@sha256:` digest;
- the toolchain comes from exactly one `ADD --checksum=sha256:... <url> /tc.tar`;
- any other download is also an `ADD --checksum`;
- nothing is read from the build context, and `RUN` does no downloads
  (the build runs with `--network=none`, so it could not anyway);
- `ENV TC_ID=<id>` (the directory name) and `ENV CC=<path to the cross gcc>`.

### Using the images to build software

Each image also has `make`, `patchelf`, `patch`, `xz`,
`bzip2` and `pkg-config`, installed from `.deb` files pinned by sha256 on
snapshot.debian.org. So the image you fingerprinted is the one you build with:

```sh
docker run --rm -v "$PWD:/work" ghcr.io/devm18426/toolchain-fingerprints:mips32-uclibc-2017.11 make
```

Bootlin toolchains put some host tools of their own in `/opt/tc/bin`, which is
first on `PATH`; the 2017.11 one, for example, has an older `patchelf` 0.9.
Autotools and a host gcc are not included; see `BUILD_TOOLS` in
`generator/gen.py` to add more.

## Images locally

Without `TCFP_REGISTRY`, `gen.py build` tags images `tcfp/<id>:<hash>` on this
machine. With it set, `build --push` publishes to that registry (pulling instead
if the hash already exists) and `pull` fetches an image instead of building:

```sh
TCFP_REGISTRY=ghcr.io/devm18426/toolchain-fingerprints python generator/gen.py pull mips32-uclibc-2017.11
```

With a registry set, `status` also counts a record as stale until its image is
in that registry.

## Compatibility

Data published later is meant to keep working with consumers written earlier.
Within schema 2.x fields are only added. Enum-like fields that may grow are open
(consumers treat unknown values as unknown), and architecture details live in
the open-ended `arch.abi`. The published schema accepts unknown fields and values;
`gen.py validate` is strict, and `gen.py validate --tolerant` checks the way a
consumer would. See `docs/DESIGN.md` section 4.4.

Adding an architecture: write a decoder in `generator/normalize.py` and register it
in `ARCH`. Machines without a decoder still get a full record; only `float_abi`
is `unknown` and `arch.abi` is empty.
