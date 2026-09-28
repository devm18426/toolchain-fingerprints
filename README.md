# Cross-toolchain ABI fingerprints

Pick a cross toolchain that will actually run on your target — without the
build-it-and-see roulette.

A cross toolchain's binaries carry a fixed set of runtime requirements: a
**loader/interp** path, a **libc soname**, an **ISA**, an **endianness**, and a
**float ABI**. If any of those don't match what your target provides in `/lib`,
the binary won't run (wrong loader, missing `.so`, `SIGILL`, …). This project
**fingerprints** those requirements per toolchain and lets you **match** them
against a target you can inspect but can't easily rebuild (routers, IoT, old
SoCs).

## Why not just match libc version?

Because version ≠ ABI. Two gotchas this catches that version-matching misses:

- A trivial `hello` **under-reports**: a hardened/large app can
  pull in the toolchain's loader as an explicit `NEEDED` — `ld-uClibc.so.1` —
  even when the target only ships `ld-uClibc.so.0`. So the fingerprint records
  the **`ldso_soname`** from the toolchain's own sysroot as a risk flag, not just
  what `hello` happens to need.
- The float ABI (`hard`/`soft`/`softfp`) and exact ISA revision (`mips32` r1 vs
  `mips32r2`) are recorded too — a `mips32r2` toolchain `SIGILL`s on an r1 core.

## Use it

```sh
# fingerprint every extracted toolchain under a directory (each exposes bin/<triple>-gcc)
./fingerprint-dir.sh ./toolchains > fingerprints.json

# or one toolchain
TC_ID=my-tc ./probe-toolchain.sh /opt/my-tc/bin/mips-linux-gcc
```

Then open `site/index.html` (or the published GitHub Pages table), paste your
target's loader and `/lib` sonames, and it flags each toolchain **OK / RISKY / NO**.

Find your target's values on-device:

```sh
readelf -l /some/target/binary | grep interp    # -> the loader/interp
ls /lib                                          # -> the sonames it provides
```

## What a fingerprint looks like

```json
{"tc_id":"mips32-uclibc-2017.11","triple":"mips-buildroot-linux-uclibc","gcc":"7.2.0",
 "libc_kind":"uclibc","elf_class":"ELF32","endian":"big endian","isa":"MIPS32",
 "float":"Hard float","default_type":"EXEC","interp":"/lib/ld-uClibc.so.0",
 "needed":"libc.so.0","ldso_soname":"ld-uClibc.so.1","libc_soname":"libc.so.0",
 "dynamic_ok":true,"static_ok":true}
```

## Publishing (GitHub Pages)

`.github/workflows/fingerprint.yml` populates `./toolchains` from the URLs in
`toolchains.txt`, runs the fingerprinter, commits `fingerprints.json`, and
deploys the table to Pages on push / weekly. Enable Pages (Settings → Pages →
Source: GitHub Actions).

The tool only ever reads local toolchain dirs; how they get there (the URL list,
a cache, a committed dir, your own step) is up to the workflow.

## Files

| file | role |
|---|---|
| `probe-toolchain.sh` | fingerprint one toolchain → one JSON object |
| `fingerprint-dir.sh` | fingerprint every toolchain under a dir → JSON array |
| `fingerprints.json` | the dataset |
| `site/index.html` | searchable table + target matcher (no build step) |
| `toolchains.txt` | URL list the CI uses to populate `./toolchains` |
| `.github/workflows/fingerprint.yml` | CI: fingerprint + publish |

## Status / scope

MIPS-first, expanding to other arches. Contributions of fingerprints for more
toolchains welcome.
