# Cross-toolchain ABI fingerprints

Answer one question without deploying to the device: *will a binary built by
toolchain X run on target Y?*

A cross toolchain's binaries carry a fixed set of runtime requirements: a
loader path, libc sonames, an ISA, endianness, a float ABI. This project
records those facts per toolchain and a static page compares them against what
your target provides. See [docs/DESIGN.md](docs/DESIGN.md) for the reasoning.

## Layout

| path | component | role |
|---|---|---|
| `schema/fingerprint.schema.json` | contract | the only thing the two components share |
| `toolchains/<id>/Dockerfile` | generator | one fully pinned toolchain per directory |
| `generator/probe/` | generator | `probe.sh` runs in the image; `normalize.py` turns its raw output into a record |
| `generator/probe/corpus/` | generator | small C/C++ programs linked by the probe (pthreads, select/clock_gettime, dlopen, libm, 64-bit division, exceptions) |
| `generator/gen.py` | generator | lint, build, probe, merge, validate (stdlib Python only) |
| `generator/tests/` | generator | `python -m unittest discover -s generator/tests` (no Docker needed) |
| `data/fingerprints.json` | output | the dataset, validated against the schema |
| `web/index.html` | web page | reads only `fingerprints.json`; no build step |

## Generating records

A pinned toolchain always produces the same binaries, so a record is computed
once. It is redone only when the toolchain's Dockerfile or the probe changes;
both are hashed into the record's `provenance`. There is no scheduled recheck.

One step per command:

```sh
python generator/gen.py status                    # what is up to date / stale
python generator/gen.py build mips32-musl-2026.08 # docker build (RUN steps offline)
python generator/gen.py probe mips32-musl-2026.08 # -> .cache/records/<id>.json
python generator/gen.py merge                     # -> data/fingerprints.json
python generator/gen.py validate                  # check against the schema
```

### Adding a toolchain

Create `toolchains/<id>/Dockerfile`. The generator refuses it (`gen.py lint`)
unless every input is pinned:

- every `FROM` is pinned by `@sha256:` digest;
- the toolchain comes from exactly one `ADD --checksum=sha256:... <url>`;
- nothing is read from the build context, and `RUN` does no downloads
  (the build runs with `--network=none`, so it could not anyway);
- `ENV TC_ID=<id>` (the directory name) and `ENV CC=<path to the cross gcc>`.

Copy an existing Dockerfile and change the URL, checksum, `TC_ID` and `CC`.

## Viewing

```sh
python -m http.server 8765
```

then open <http://localhost:8765/web/>. Fill in what you know about the target
(loader, `/lib` sonames, `uname -r`, endianness, machine, glibc version), or
paste `readelf -h -l -d`, `ls /lib` and `uname -r` output, and each toolchain
gets **OK / RISKY / NO** with the reasons. Rules only fire for facts you gave.

What the matcher checks:

| verdict | when |
|---|---|
| NO | wrong endianness or machine; different loader; a `hello` NEEDED lib missing; libc uses only `*_time64` syscalls and the kernel is older than 5.1; the kernel is below glibc's minimum; the program needs newer `GLIBC_` symbols than the target has |
| RISKY | a library that real programs (pthreads, `select`, `dlopen`, libm) pull in is missing, e.g. `ld-uClibc.so.1`; only `DT_GNU_HASH` against a uClibc loader |
| INFO | libc uses `*_time64` with fallback; kernel headers newer than the target kernel |

The page refuses a dataset whose schema major version it does not know.

`.github/workflows/pages.yml` publishes `web/index.html` next to
`data/fingerprints.json` on push. It lints, validates and checks that every
record is current, but never fingerprints anything itself.
