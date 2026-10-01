#!/usr/bin/env python3
"""Toolchain fingerprint generator.

Each step is its own command, so a failure points at one step:

    gen.py new      TC_ID URL     download a toolchain tarball, hash it, write toolchains/TC_ID/Dockerfile
    gen.py import-bootlin RELEASE  do that for every Bootlin toolchain of a release (filters available)
    gen.py status                 what is up to date, stale, or invalid (read-only)
    gen.py lint     [TC_ID...]    check that every Dockerfile input is pinned
    gen.py build    TC_ID [--push] docker build toolchains/TC_ID (RUN steps have no network)
    gen.py pull     TC_ID         fetch the image from the registry instead of building it
    gen.py probe    TC_ID         run the probe in the image -> .cache/records/TC_ID.json
    gen.py merge                  merge current records into data/fingerprints.json
    gen.py validate [FILE...]     validate records or a data file against the schema

A record is computed once. It is current exactly as long as its provenance
(Dockerfile hash + probe version) matches the repo; there is no periodic recheck.

Only the Python standard library is used. The generator knows nothing about how
the data is displayed or matched: its only output is data/fingerprints.json,
which must validate against schema/fingerprint.schema.json.

Images are content-addressed: the tag is the Dockerfile hash. With
TCFP_REGISTRY set (e.g. ghcr.io/devm18426/toolchain-fingerprints) images are
<registry>:<tc_id>-<hash12>; without it they stay local as tcfp/<tc_id>:<hash12>.
"""
import argparse
import datetime
import os
import hashlib
import io
import json
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import concurrent.futures
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema" / "fingerprint.schema.json"
PROBE_DIR = ROOT / "generator" / "probe"
TOOLCHAINS = ROOT / "toolchains"
NORMALIZER = ROOT / "generator" / "normalize.py"
CACHE = ROOT / ".cache" / "records"
RAW_CACHE = ROOT / ".cache" / "raw"
DATA = ROOT / "data" / "fingerprints.json"
RAW_DATA = ROOT / "data" / "raw"            # probe output per toolchain: records re-derive from it
# Toolchain tarballs contain x86-64 host binaries; the base images are multi-arch
# indexes, so pin the platform or an arm64 host would pull the wrong base.
PLATFORM = "linux/amd64"
TOOLCHAIN_DEST = "/tc.tar"          # the ADD with this destination is the toolchain

# Pinned inputs every toolchain Dockerfile gets (gen.py new writes them).
ALPINE = "alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8"
DEBIAN = "debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251"
# Build tools for using the image to build software, as .debs from the same
# snapshot.debian.org date the DEBIAN image was built from; hashes match apt's index.
_SNAP = "https://snapshot.debian.org/archive/debian/20260918T000000Z/pool/main"
_SNAP_SEC = "https://snapshot.debian.org/archive/debian-security/20260918T000000Z/pool/updates/main"
BUILD_TOOLS = [
    (f"{_SNAP}/m/make-dfsg/make_4.3-4.1_amd64.deb", "a1a83af8cbd854af887b72ad196b1f4af58387815e21ced1000253a116a46e2a"),
    (f"{_SNAP}/p/patchelf/patchelf_0.14.3-1+b1_amd64.deb", "0364b90e81faabbb9569076063952c7bcbf07f83d954a2f2602a163d65be60e6"),
    (f"{_SNAP}/p/patch/patch_2.7.6-7_amd64.deb", "8c6d49b771530dbe26d7bd060582dc7d2b4eeb603a20789debc1ef4bbbc4ef67"),
    (f"{_SNAP_SEC}/x/xz-utils/xz-utils_5.4.1-1+deb12u2_amd64.deb", "f99fc3fe4b4e5baecf5fd8b53853a82e1e71cb5ec187b36b0ddcf227a8961c8f"),
    (f"{_SNAP}/b/bzip2/bzip2_1.0.8-5+b1_amd64.deb", "438871b3f5c5c7a357a9840951dab9dab8db7eb1ff760a563226fafa111b99e5"),
    (f"{_SNAP}/p/pkgconf/libpkgconf3_1.8.1-1_amd64.deb", "da01fb901123ae498c36387a32240e09e1f2866810146c5a574273f7eaf31093"),
    (f"{_SNAP}/p/pkgconf/pkgconf-bin_1.8.1-1_amd64.deb", "8fb5a8f83e46ad04b4cf02651ceec56c0611a335cf0d30780d859a95d0400174"),
    (f"{_SNAP}/p/pkgconf/pkgconf_1.8.1-1_amd64.deb", "4e3ce982b5fedc6c6119268435504a64f5ffcc6d93aaecaea902d816eba1215f"),
    (f"{_SNAP}/p/pkgconf/pkg-config_1.8.1-1_amd64.deb", "312b2bdeff4671f8e0d589c124554890e944dd083061e9ad6f129bc76a970765"),
]

sys.path.insert(0, str(NORMALIZER.parent))
from normalize import normalize  # noqa: E402


def die(msg):
    print(f"gen: {msg}", file=sys.stderr)
    sys.exit(1)


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", newline="\n")


def lf_bytes(p):
    return p.read_bytes().replace(b"\r\n", b"\n")


def schema():
    return json.loads(SCHEMA.read_text())


def schema_version():
    """The version this generator writes: the schema file's own x-version."""
    return schema()["x-version"]


# --------------------------------------------------------------------------
# Minimal JSON Schema validator (the subset fingerprint.schema.json uses)
# --------------------------------------------------------------------------
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is_type(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def validate(inst, sch, root=None, path="$", strict=False):
    """Return a list of error strings; empty means valid.

    The published schema is deliberately tolerant so that consumers holding an
    older 2.x schema still accept newer 2.x data: objects allow unknown
    properties and open enums are plain strings listing x-known-values.
    strict=True (what the generator uses) additionally rejects unknown
    properties and values outside x-known-values, so typos cannot ship.
    """
    root = root if root is not None else sch
    errs = []
    if "$ref" in sch:
        node = root
        for part in sch["$ref"].lstrip("#/").split("/"):
            node = node[part]
        errs += validate(inst, node, root, path, strict)
    if "type" in sch:
        types = sch["type"] if isinstance(sch["type"], list) else [sch["type"]]
        if not any(_is_type(inst, t) for t in types):
            return errs + [f"{path}: expected {'/'.join(types)}, got {type(inst).__name__}"]
    if "enum" in sch and inst not in sch["enum"]:
        errs.append(f"{path}: {inst!r} not in {sch['enum']}")
    if strict and "x-known-values" in sch and inst not in sch["x-known-values"]:
        errs.append(f"{path}: {inst!r} not in x-known-values {sch['x-known-values']} (add it to the schema)")
    if "const" in sch and inst != sch["const"]:
        errs.append(f"{path}: must be {sch['const']!r}")
    if isinstance(inst, str) and "pattern" in sch and not re.search(sch["pattern"], inst):
        errs.append(f"{path}: {inst!r} does not match {sch['pattern']}")
    if isinstance(inst, (int, float)) and not isinstance(inst, bool) and "minimum" in sch and inst < sch["minimum"]:
        errs.append(f"{path}: {inst} < minimum {sch['minimum']}")
    if isinstance(inst, dict):
        for k in sch.get("required", []):
            if k not in inst:
                errs.append(f"{path}: missing required '{k}'")
        props = sch.get("properties", {})
        extra = sch.get("additionalProperties", False if (strict and "properties" in sch) else True)
        for k, v in inst.items():
            if k in props:
                errs += validate(v, props[k], root, f"{path}.{k}", strict)
            elif extra is False:
                errs.append(f"{path}: unexpected property '{k}' (add it to the schema)")
            elif isinstance(extra, dict):
                errs += validate(v, extra, root, f"{path}.{k}", strict)
    if isinstance(inst, list) and "items" in sch:
        for i, v in enumerate(inst):
            errs += validate(v, sch["items"], root, f"{path}[{i}]", strict)
    return errs


def record_errors(rec, strict=True):
    return validate(rec, {"$ref": "#/$defs/record"}, schema(), strict=strict)


def data_errors(doc, strict=True):
    errs = validate(doc, schema(), strict=strict)
    ids = [r.get("tc_id") for r in doc.get("toolchains", []) if isinstance(r, dict)]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        errs.append(f"$.toolchains: duplicate tc_id {sorted(dup)}")
    return errs


# --------------------------------------------------------------------------
# Provenance inputs
# --------------------------------------------------------------------------
def normalize_version():
    """sha256 of generator/normalize.py. A change only needs records re-derived from
    data/raw/, not new probe runs, so it never costs a Docker build."""
    return hashlib.sha256(lf_bytes(NORMALIZER)).hexdigest()


def probe_version():
    """sha256 over every file in generator/probe/ (what runs inside the image)."""
    h = hashlib.sha256()
    for p in sorted(PROBE_DIR.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(p.relative_to(PROBE_DIR).as_posix().encode() + b"\0")
            h.update(lf_bytes(p) + b"\0")
    return h.hexdigest()


def dockerfile(tc_id):
    return TOOLCHAINS / tc_id / "Dockerfile"


def dockerfile_sha256(tc_id):
    return hashlib.sha256(lf_bytes(dockerfile(tc_id))).hexdigest()


def toolchain_ids():
    return sorted(p.parent.name for p in TOOLCHAINS.glob("*/Dockerfile"))


def registry():
    return os.environ.get("TCFP_REGISTRY", "").rstrip("/")


def image_tag(tc_id):
    h = dockerfile_sha256(tc_id)[:12]
    return f"{registry()}:{tc_id}-{h}" if registry() else f"tcfp/{tc_id}:{h}"


# --------------------------------------------------------------------------
# Lint: refuse Dockerfiles with unpinned inputs
# --------------------------------------------------------------------------
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
SHA = re.compile(r"^sha256:([0-9a-f]{64})$")


def instructions(text):
    """Yield (lineno, KEYWORD, args) with continuations joined and comments dropped."""
    buf, start = "", None
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not buf and (not s or s.startswith("#")):
            continue
        if buf and s.startswith("#"):
            continue
        start = start or n
        if s.endswith("\\"):
            buf += s[:-1] + " "
            continue
        buf += s
        kw, _, args = buf.partition(" ")
        yield start, kw.upper(), args.strip()
        buf, start = "", None


def _flags(args):
    toks = shlex.split(args)
    flags = {}
    while toks and toks[0].startswith("--"):
        k, _, v = toks.pop(0)[2:].partition("=")
        flags[k] = v
    return flags, toks


def lint(tc_id):
    """Return (errors, facts). facts has toolchain_sha256, TC_ID, CC when found."""
    path = dockerfile(tc_id)
    errs, facts, stages, tc_sums, env, labels = [], {}, set(), [], {}, {}
    for n, kw, args in instructions(path.read_text()):
        where = f"{path.relative_to(ROOT).as_posix()}:{n}"
        flags, toks = _flags(args)
        if kw == "FROM":
            labels = {}                                  # only the final stage's labels reach the image
            ref = toks[0] if toks else ""
            if "$" in ref:
                errs.append(f"{where}: FROM uses a variable ({ref}); pin the image literally")
            elif ref not in stages and ref != "scratch" and not DIGEST.search(ref):
                errs.append(f"{where}: FROM {ref} is not pinned by @sha256 digest")
            if len(toks) >= 3 and toks[1].upper() == "AS":
                stages.add(toks[2])
        elif kw in ("ADD", "COPY"):
            srcs = toks[:-1]
            frm = flags.get("from")
            if frm is not None:
                if frm not in stages and not DIGEST.search(frm):
                    errs.append(f"{where}: {kw} --from={frm} is neither a stage nor pinned by digest")
                continue
            for s in srcs:
                if re.match(r"^(https?|git|ssh)://|^git@|\.git(#|$)", s):
                    m = SHA.match(flags.get("checksum", ""))
                    if kw != "ADD" or not m or s.startswith(("git", "ssh")) or ".git" in s:
                        errs.append(f"{where}: {s} is downloaded without --checksum=sha256:...")
                    elif toks[-1] == TOOLCHAIN_DEST:
                        tc_sums.append(m.group(1))
                        facts["toolchain_url"] = s
                else:
                    errs.append(f"{where}: {kw} {s} reads the build context, which the Dockerfile hash "
                                f"does not cover; download it with ADD --checksum instead")
        elif kw == "RUN":
            for f in flags:
                if f in ("network", "mount", "security"):
                    errs.append(f"{where}: RUN --{f} is not allowed (RUN steps must be offline and hermetic)")
            m = re.search(r"\b(wget|curl|apt-get|apt|apk|pip3?|git|npm|opkg|yum|dnf)\b", args)
            if m:
                errs.append(f"{where}: RUN uses {m.group(1)}; RUN steps build without network, so every "
                            f"download must be an ADD --checksum=sha256:... instead")
        elif kw == "ONBUILD":
            errs.append(f"{where}: ONBUILD is not allowed")
        elif kw == "ENV":
            for t in shlex.split(args):
                k, eq, v = t.partition("=")
                if eq:
                    env[k] = v
        elif kw == "LABEL":
            for t in shlex.split(args):
                k, eq, v = t.partition("=")
                if eq:
                    labels[k] = v
    if len(tc_sums) != 1:
        errs.append(f"{path.relative_to(ROOT).as_posix()}: expected exactly one checksummed toolchain "
                    f"download (ADD --checksum=sha256:... URL {TOOLCHAIN_DEST}), found {len(tc_sums)}")
    else:
        facts["toolchain_sha256"] = tc_sums[0]
    if env.get("TC_ID") != tc_id:
        errs.append(f"{path.relative_to(ROOT).as_posix()}: ENV TC_ID must be '{tc_id}' (the directory name), "
                    f"found {env.get('TC_ID')!r}")
    if not env.get("CC"):
        errs.append(f"{path.relative_to(ROOT).as_posix()}: ENV CC must name the cross gcc")
    if len(tc_sums) == 1:
        for k, v in static_labels(tc_id, facts.get("toolchain_url", ""), tc_sums[0]).items():
            if labels.get(k) != v:
                errs.append(f"{path.relative_to(ROOT).as_posix()}: final stage needs LABEL {k}=\"{v}\" "
                            f"(found {labels.get(k)!r})")
    return errs, facts


# --------------------------------------------------------------------------
# Scaffolding a new toolchain
# --------------------------------------------------------------------------
def static_labels(tc_id, url, sha256):
    """Labels that describe the toolchain. They live in the Dockerfile (plain
    docker build gets them) because they only change when the toolchain does.
    Labels that change for other reasons (repo URL, commit, the Dockerfile's own
    hash) are added at build time instead."""
    return {
        "org.opencontainers.image.title": tc_id,
        "io.tcfp.tc_id": tc_id,
        "io.tcfp.toolchain.url": url,
        "io.tcfp.toolchain.sha256": sha256,
    }


def dockerfile_text(tc_id, comment, url, sha256, cc):
    labels = " \\\n      ".join(f'{k}="{v}"' for k, v in static_labels(tc_id, url, sha256).items())
    tools = "".join(f"ADD --checksum=sha256:{h} \\\n    {u} /debs/\n" for u, h in BUILD_TOOLS)
    return f"""# {comment}
# Every input is pinned: base images by digest, downloads by sha256.
# RUN steps are built with --network=none, so nothing unpinned can be fetched.

FROM {ALPINE} AS fetch
ADD --checksum=sha256:{sha256} \\
    {url} {TOOLCHAIN_DEST}
RUN mkdir /tc && tar -xf {TOOLCHAIN_DEST} -C /tc --strip-components=1

FROM {DEBIAN}
# build tools (make, patchelf, patch, xz, bzip2, pkg-config), pinned .debs
{tools}RUN dpkg -i /debs/*.deb && rm -rf /debs
COPY --from=fetch /tc /opt/tc
ENV TC_ID={tc_id} \\
    CC=/opt/tc/bin/{cc} \\
    PATH=/opt/tc/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /work
LABEL {labels}
"""


class _Hashing(io.RawIOBase):
    """Wraps a byte stream and hashes everything read through it."""

    def __init__(self, f):
        self.f, self.h = f, hashlib.sha256()

    def readable(self):
        return True

    def readinto(self, b):
        n = self.f.readinto(b)
        if n:
            self.h.update(memoryview(b)[:n])
        return n


def inspect_tarball(url):
    """Stream url once: return (sha256, top-level dirs, gcc names in <top>/bin/).

    Nothing is written to disk, so importing hundreds of toolchains needs no
    space. curl is preferred: it verifies TLS with the OS certificate store,
    which Python's bundled store can disagree with.
    """
    proc = None
    if shutil.which("curl"):
        proc = subprocess.Popen(["curl", "-fsSL", "--retry", "3", url], stdout=subprocess.PIPE)
        raw = proc.stdout
    else:
        raw = urllib.request.urlopen(url)
    src = _Hashing(raw)
    buf = io.BufferedReader(src, 1 << 20)
    tops, gccs = set(), set()
    try:
        with tarfile.open(fileobj=buf, mode="r|*") as t:
            for m in t:
                parts = m.name.lstrip("./").split("/")
                if parts[0]:
                    tops.add(parts[0])
                if len(parts) == 3 and parts[1] == "bin" and re.fullmatch(r"[\w.+-]+-gcc", parts[2]):
                    gccs.add(parts[2])
        while buf.read(1 << 20):                      # trailing padding: hash the whole file
            pass
    except tarfile.TarError as e:
        raise ValueError(f"not a readable tarball: {e}")
    finally:
        if proc:
            proc.stdout.close()
            rc = proc.wait()
            if rc:
                raise ValueError(f"download failed (curl exit {rc})")
    # prefer the full triple (most dashes), e.g. mips-buildroot-linux-uclibc-gcc over mips-linux-gcc
    return src.h.hexdigest(), tops, sorted(gccs, key=lambda g: (-g.count("-"), g))


def scaffold(tc_id, url, cc=None, comment=None):
    """Write toolchains/TC_ID/Dockerfile for a tarball. Returns (cc, sha256); raises ValueError."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", tc_id):
        raise ValueError(f"bad TC_ID {tc_id!r}: use letters, digits, '.', '_' and '-'")
    if (TOOLCHAINS / tc_id).exists():
        raise ValueError(f"toolchains/{tc_id} already exists")
    sha, tops, gccs = inspect_tarball(url)
    if len(tops) != 1:
        raise ValueError(f"expected one top-level directory in the tarball (it is extracted with "
                         f"--strip-components=1), found {sorted(tops)[:5]}")
    if cc and cc not in gccs:
        raise ValueError(f"--cc {cc} is not in the tarball's bin/ (found: {', '.join(gccs) or 'none'})")
    cc = cc or (gccs[0] if gccs else None)
    if not cc:
        raise ValueError("no <top>/bin/*-gcc in the tarball; pass --cc NAME")
    df = dockerfile(tc_id)
    df.parent.mkdir(parents=True)
    df.write_text(dockerfile_text(tc_id, comment or url.rsplit("/", 1)[-1], url, sha, cc), newline="\n")
    errs, _ = lint(tc_id)
    if errs:
        shutil.rmtree(df.parent)
        raise ValueError("generated Dockerfile does not lint (this is a bug):\n  " + "\n  ".join(errs))
    return cc, sha


def cmd_new(a):
    print(f"downloading {a.url}", flush=True)
    try:
        cc, sha = scaffold(a.tc_id, a.url, a.cc, a.comment)
    except ValueError as e:
        die(str(e))
    print(f"sha256 {sha}\nCC {cc}\nwrote toolchains/{a.tc_id}/Dockerfile\nnext, one at a time:\n"
          f"  python generator/gen.py build {a.tc_id}\n"
          f"  python generator/gen.py probe {a.tc_id}\n"
          f"  python generator/gen.py merge")


# --------------------------------------------------------------------------
# Bulk import from Bootlin
# --------------------------------------------------------------------------
BOOTLIN = "https://toolchains.bootlin.com/downloads/releases/toolchains"


def fetch_text(url):
    if shutil.which("curl"):
        p = subprocess.run(["curl", "-fsSL", "--retry", "3", url], capture_output=True)
        if p.returncode:
            raise ValueError(f"{url}: curl exit {p.returncode}")
        return p.stdout.decode("utf-8", "replace")
    with urllib.request.urlopen(url) as r:
        return r.read().decode("utf-8", "replace")


def bootlin_tarballs(release, archs=(), libcs=(), channels=()):
    """Yield (tc_id, url) for every Bootlin tarball of RELEASE matching the filters.

    Bootlin names tarballs <arch>--<libc>--<stable|bleeding-edge>-<release>.tar.*;
    the id is <arch>-<libc>-<channel>-<release>.
    """
    index = fetch_text(BOOTLIN + "/")
    arch_dirs = sorted(set(re.findall(r'href="([A-Za-z0-9._+-]+)/"', index)))
    if archs:
        arch_dirs = [d for d in arch_dirs if d in archs]
    pat = re.compile(r"(?P<arch>[A-Za-z0-9._+-]+?)--(?P<libc>[a-z]+)--(?P<ch>stable|bleeding-edge)-"
                     + re.escape(release) + r"\.tar\.(xz|bz2|gz)")

    def listing(arch):
        return arch, fetch_text(f"{BOOTLIN}/{arch}/tarballs/")

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        pages = list(pool.map(listing, arch_dirs))
    for arch, page in pages:
        for name in sorted(set(re.findall(r'href="([^"/?]+\.tar\.(?:xz|bz2|gz))"', page))):
            m = pat.fullmatch(name)
            if not m or m["arch"] != arch:
                continue
            if (libcs and m["libc"] not in libcs) or (channels and m["ch"] not in channels):
                continue
            yield f"{arch}-{m['libc']}-{m['ch']}-{release}", f"{BOOTLIN}/{arch}/tarballs/{name}"


def cmd_import_bootlin(a):
    have_urls = {lint(tc)[1].get("toolchain_url") for tc in toolchain_ids()}
    todo, skipped = [], []
    for tc_id, url in bootlin_tarballs(a.release, set(a.arch), set(a.libc), set(a.channel)):
        if url in have_urls or (TOOLCHAINS / tc_id).exists():
            skipped.append(tc_id)
        else:
            todo.append((tc_id, url))
    if a.limit:
        todo = todo[:a.limit]
    print(f"Bootlin {a.release}: {len(todo)} to import, {len(skipped)} already present", flush=True)
    if a.dry_run:
        for tc_id, url in todo:
            print(f"  {tc_id}  {url}")
        return
    done, failed = [], []

    def one(item):
        tc_id, url = item
        try:
            cc, _ = scaffold(tc_id, url, comment=f"Bootlin {url.rsplit('/', 1)[-1]}")
            return tc_id, cc, None
        except (ValueError, OSError) as e:
            return tc_id, None, str(e).splitlines()[0]

    with concurrent.futures.ThreadPoolExecutor(a.jobs) as pool:
        for tc_id, cc, err in pool.map(one, todo):
            (failed if err else done).append((tc_id, err or cc))
            print(f"  {'FAILED' if err else 'ok    '} {tc_id}  {err or cc}", flush=True)
    print(f"imported {len(done)}, failed {len(failed)}, skipped {len(skipped)}")
    if a.summary:
        lines = [f"Imports {len(done)} Bootlin `{a.release}` toolchains with `gen.py import-bootlin`.", ""]
        if failed:
            lines += [f"{len(failed)} tarballs could not be imported:", ""]
            lines += [f"- `{t}`: {e}" for t, e in failed] + [""]
        if skipped:
            lines += [f"{len(skipped)} were already present and skipped.", ""]
        Path(a.summary).write_text("\n".join(lines) + "\n", encoding="utf-8")
    if todo and not done:
        sys.exit(2)


# --------------------------------------------------------------------------
# Docker
# --------------------------------------------------------------------------
def image_id(tag):
    try:
        p = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", tag],
                           capture_output=True, text=True)
    except FileNotFoundError:          # no docker here (e.g. CI publishing only)
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def image_ref(tag):
    """Pullable registry digest (repo@sha256:...) if the image was pushed/pulled, else the local ID."""
    if not registry():
        return image_id(tag)
    p = subprocess.run(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", tag],
                       capture_output=True, text=True)
    repo = tag.rsplit(":", 1)[0]
    for d in (json.loads(p.stdout) if p.returncode == 0 else []) or []:
        if d.startswith(repo + "@"):
            return d
    return image_id(tag)


def try_pull(tag):
    """Pull tag if the registry has it. True means it exists (and is now local)."""
    p = subprocess.run(["docker", "pull", "-q", "--platform", PLATFORM, tag], capture_output=True, text=True)
    return p.returncode == 0


def docker(*args):
    cmd = ["docker", *args]
    print("+ " + " ".join(cmd), flush=True)
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        die(f"{' '.join(cmd[:2])} failed (exit {rc})")


def probe_tar():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for p in sorted(PROBE_DIR.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                data = lf_bytes(p)
                info = tarfile.TarInfo(p.relative_to(PROBE_DIR).as_posix())
                info.size, info.mode = len(data), 0o755
                t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def run_probe(image):
    """Send the probe into the container on stdin; get the raw facts back as a tar on stdout."""
    cmd = ["docker", "run", "--rm", "-i", "--network=none", "--platform", PLATFORM,
           "--entrypoint", "/bin/sh", image, "-c",
           "mkdir -p /probe && tar -xf - -C /probe && exec bash /probe/probe.sh"]
    p = subprocess.run(cmd, input=probe_tar(), capture_output=True)
    if p.returncode != 0:
        die(f"probe failed in {image} (exit {p.returncode}):\n{p.stderr.decode(errors='replace')}")
    raw = {}
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as t:
        for m in t.getmembers():
            if m.isfile():
                raw[m.name.removeprefix("./")] = t.extractfile(m).read().decode(errors="replace")
    return raw


# --------------------------------------------------------------------------
# Record bookkeeping
#
# A record depends on three inputs:
#   Dockerfile + probe (+ registry)  -> the raw probe output (needs Docker to redo)
#   normalize.py                     -> the record, re-derived from that raw output
# --------------------------------------------------------------------------
def load_data():
    return {r["tc_id"]: r for r in json.loads(DATA.read_text())["toolchains"]} if DATA.exists() else {}


def load_cache():
    out = {}
    for f in sorted(CACHE.glob("*.json")):
        r = json.loads(f.read_text())
        out[r["tc_id"]] = r
    return out


def load_raw(directory, tc_id):
    f = directory / f"{tc_id}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def in_registry(rec):
    """With TCFP_REGISTRY set, a record only counts if its image came from that registry."""
    return not registry() or (rec or {}).get("provenance", {}).get("image_digest", "").startswith(registry() + "@")


def probe_current(rec, tc_id, pv):
    """The raw probe output behind rec is still valid (no Docker work needed)."""
    p = (rec or {}).get("provenance", {})
    return (p.get("dockerfile_sha256") == dockerfile_sha256(tc_id) and p.get("probe_version") == pv
            and in_registry(rec))


def is_current(rec, tc_id, pv, nv):
    return probe_current(rec, tc_id, pv) and rec["provenance"].get("normalize_version") == nv


def stale_reason(rec, tc_id, pv, nv=None):
    if not rec:
        return "never probed"
    p = rec.get("provenance", {})
    why = []
    if p.get("dockerfile_sha256") != dockerfile_sha256(tc_id):
        why.append("Dockerfile changed")
    if p.get("probe_version") != pv:
        why.append("probe changed")
    if not in_registry(rec):
        why.append(f"image not in {registry()}")
    if nv and p.get("normalize_version") != nv:
        why.append("normalizer changed")
    return ", ".join(why)


def rederive(rec, raw, tc_id, nv):
    """Re-run normalize.py on stored raw output, keeping the probe's provenance."""
    new = normalize(raw, tc_id)
    new["provenance"] = {**rec["provenance"], "normalize_version": nv}
    return new


def state_of(tc, data, cache, pv, nv):
    """current | renormalize (merge fixes it, no Docker) | probed (merge) | stale (needs a probe) | refused"""
    if lint(tc)[0]:
        return "refused"
    if is_current(data.get(tc), tc, pv, nv):
        return "current"
    if probe_current(cache.get(tc), tc, pv) and load_raw(RAW_CACHE, tc):
        return "probed"
    if probe_current(data.get(tc), tc, pv) and load_raw(RAW_DATA, tc):
        return "renormalize"
    return "stale"


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def need_toolchain(tc_id):
    if not dockerfile(tc_id).exists():
        die(f"no toolchains/{tc_id}/Dockerfile (have: {', '.join(toolchain_ids()) or 'none'})")


def need_lint(tc_id):
    errs, facts = lint(tc_id)
    if errs:
        die("refusing unpinned Dockerfile:\n  " + "\n  ".join(errs))
    return facts


def cmd_lint(a):
    bad = 0
    for tc in a.tc_ids or toolchain_ids():
        need_toolchain(tc)
        errs, _ = lint(tc)
        print(f"{tc}: {'ok' if not errs else 'REFUSED'}")
        for e in errs:
            print(f"  {e}")
        bad += bool(errs)
    sys.exit(1 if bad else 0)


def cmd_build(a):
    need_toolchain(a.tc_id)
    need_lint(a.tc_id)
    tag = image_tag(a.tc_id)
    if a.push and not registry():
        die("--push needs TCFP_REGISTRY (e.g. ghcr.io/<owner>/toolchain-fingerprints)")
    if a.push and try_pull(tag):
        print(f"{tag} already in the registry (same Dockerfile hash); pulled it instead of building")
    else:
        # Toolchain labels are LABELs in the Dockerfile (see static_labels). Labels that
        # change for other reasons are added here, so they never change the Dockerfile
        # hash: the repo URL and commit (passed by CI) and the Dockerfile's own hash.
        labels = [f"--label={l}" for l in a.label] + [
            f"--label=io.tcfp.dockerfile_sha256={dockerfile_sha256(a.tc_id)}"]
        docker("build", "--network=none", "--platform", PLATFORM, *labels, "-t", tag, str(TOOLCHAINS / a.tc_id))
        if a.push:
            docker("push", tag)
    print(f"image {tag} = {image_ref(tag)}")


def cmd_pull(a):
    need_toolchain(a.tc_id)
    if not registry():
        die("pull needs TCFP_REGISTRY (e.g. ghcr.io/<owner>/toolchain-fingerprints)")
    tag = image_tag(a.tc_id)
    docker("pull", "--platform", PLATFORM, tag)
    print(f"image {tag} = {image_ref(tag)}")


def cmd_probe(a):
    need_toolchain(a.tc_id)
    facts = need_lint(a.tc_id)
    tag = image_tag(a.tc_id)
    if not image_id(tag):
        die(f"image {tag} not present; run: gen.py build {a.tc_id} (or gen.py pull {a.tc_id})")
    iid = image_ref(tag)
    raw = run_probe(tag)
    if raw.get("tc_id", "").strip() != a.tc_id:
        die(f"image reports TC_ID {raw.get('tc_id', '').strip()!r}, expected {a.tc_id!r}")
    rec = normalize(raw, a.tc_id)
    rec["provenance"] = {
        "dockerfile": f"toolchains/{a.tc_id}/Dockerfile",
        "dockerfile_sha256": dockerfile_sha256(a.tc_id),
        "image_digest": iid,
        "toolchain_sha256": facts["toolchain_sha256"],
        "probe_version": probe_version(),
        "normalize_version": normalize_version(),
    }
    errs = record_errors(rec)
    if errs:
        die("record does not validate:\n  " + "\n  ".join(errs))
    write_json(RAW_CACHE / f"{a.tc_id}.json", dict(sorted(raw.items())))
    out = CACHE / f"{a.tc_id}.json"
    write_json(out, rec)
    print(f"wrote {out.relative_to(ROOT).as_posix()} (+ raw probe output)")


def cmd_status(a):
    pv, nv, data, cache = probe_version(), normalize_version(), load_data(), load_cache()
    ids = toolchain_ids()
    rows = []
    for tc in ids:
        st = state_of(tc, data, cache, pv, nv)
        reason = {"stale": stale_reason(data.get(tc), tc, pv, nv), "renormalize": "normalizer changed",
                  "refused": "unpinned inputs, see gen.py lint"}.get(st, "")
        rows.append({"tc_id": tc, "state": st, "image": image_tag(tc), "reason": reason})
    orphans = sorted(set(data) - set(ids))
    if a.json:
        print(json.dumps({"toolchains": rows, "orphans": orphans,
                          "stale": [r["tc_id"] for r in rows if r["state"] == "stale"],
                          "renormalize": [r["tc_id"] for r in rows if r["state"] == "renormalize"]}))
        sys.exit(0)
    text = {"current": "up to date", "probed": "probed, run gen.py merge",
            "renormalize": "normalizer changed; gen.py merge re-derives it (no Docker)"}
    for r in rows:
        st = r["state"]
        if st == "stale":
            built = "built" if image_id(r["image"]) else "not built"
            msg = f"stale ({r['reason']}), image {built}"
        elif st == "refused":
            msg = f"REFUSED: see gen.py lint {r['tc_id']}"
        else:
            msg = text[st]
        print(f"{r['tc_id']:28} {msg}")
    for tc in orphans:
        print(f"{tc:28} orphan record (no Dockerfile); gen.py merge drops it")
    sys.exit(0 if all(r["state"] == "current" for r in rows) and not orphans else 1)


def cmd_merge(a):
    pv, nv, data, cache, ids = probe_version(), normalize_version(), load_data(), load_cache(), set(toolchain_ids())
    recs, raws, stale = {}, {}, []
    for tc in sorted(ids):
        craw, draw = load_raw(RAW_CACHE, tc), load_raw(RAW_DATA, tc)
        if probe_current(cache.get(tc), tc, pv) and craw:
            recs[tc], raws[tc] = rederive(cache[tc], craw, tc, nv), craw
        elif probe_current(data.get(tc), tc, pv) and draw:
            recs[tc], raws[tc] = rederive(data[tc], draw, tc, nv), draw
        elif tc in data:
            recs[tc] = data[tc]
            stale.append(f"{tc} ({stale_reason(data[tc], tc, pv, nv)})")
        else:
            stale.append(f"{tc} (never probed, left out)")
    for tc, r in recs.items():
        errs = record_errors(r)
        if errs:
            die(f"record {tc} does not validate:\n  " + "\n  ".join(errs))
    doc = {
        "schema_version": schema_version(),
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "toolchains": [recs[k] for k in sorted(recs)],
    }
    errs = data_errors(doc)
    if errs:
        die("merged data does not validate:\n  " + "\n  ".join(errs))
    old = json.loads(DATA.read_text()) if DATA.exists() else None
    if old and old["toolchains"] == doc["toolchains"] and old["schema_version"] == doc["schema_version"]:
        doc["generated_at"] = old["generated_at"]          # no change: keep the file byte-identical
    write_json(DATA, doc)
    for tc, raw in raws.items():
        write_json(RAW_DATA / f"{tc}.json", raw)
    for f in RAW_DATA.glob("*.json"):
        if f.stem not in recs:
            f.unlink()
    print(f"wrote {DATA.relative_to(ROOT).as_posix()} ({len(recs)} toolchains) and data/raw/")
    dropped = sorted(set(data) - ids)
    if dropped:
        print(f"dropped orphan records: {', '.join(dropped)}")
    if stale:
        print("warning: not current, re-probe: " + "; ".join(stale), file=sys.stderr)


def cmd_validate(a):
    files = a.files or [str(DATA)]
    bad = 0
    for f in files:
        doc = json.loads(Path(f).read_text())
        errs = (data_errors(doc, not a.tolerant) if "schema_version" in doc
                else record_errors(doc, not a.tolerant))
        for e in errs:
            print(f"{f}: {e}")
        if not errs:
            print(f"{f}: ok")
        bad += bool(errs)
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new", help="scaffold toolchains/TC_ID/Dockerfile from a tarball URL")
    p.add_argument("tc_id")
    p.add_argument("url")
    p.add_argument("--cc", help="gcc name in the tarball's bin/ (default: the longest *-gcc)")
    p.add_argument("--comment", help="first line of the Dockerfile (default: tarball name)")
    p.set_defaults(fn=cmd_new)
    p = sub.add_parser("import-bootlin", help="scaffold every Bootlin toolchain of a release")
    p.add_argument("release", help="Bootlin release, e.g. 2026.08-1")
    p.add_argument("--arch", nargs="*", default=[], help="only these arch directories, e.g. aarch64 mips32")
    p.add_argument("--libc", nargs="*", default=[], help="only these: glibc musl uclibc")
    p.add_argument("--channel", nargs="*", default=[], help="only these: stable bleeding-edge")
    p.add_argument("--limit", type=int, default=0, help="import at most N")
    p.add_argument("--jobs", type=int, default=4, help="parallel downloads")
    p.add_argument("--dry-run", action="store_true", help="list what would be imported")
    p.add_argument("--summary", help="write a markdown summary here (used as the PR body)")
    p.set_defaults(fn=cmd_import_bootlin)
    p = sub.add_parser("status", help="show what is up to date")
    p.add_argument("--json", action="store_true", help="machine-readable, always exits 0")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("lint", help="check Dockerfiles are fully pinned")
    p.add_argument("tc_ids", nargs="*")
    p.set_defaults(fn=cmd_lint)
    p = sub.add_parser("build", help="build one toolchain image")
    p.add_argument("tc_id")
    p.add_argument("--push", action="store_true", help="push to TCFP_REGISTRY (skips the build if the tag exists)")
    p.add_argument("--label", action="append", default=[], help="extra image label k=v (repeatable)")
    p.set_defaults(fn=cmd_build)
    for name, fn, h in (("pull", cmd_pull, "pull one toolchain image from TCFP_REGISTRY"),
                        ("probe", cmd_probe, "probe one toolchain image")):
        p = sub.add_parser(name, help=h)
        p.add_argument("tc_id")
        p.set_defaults(fn=fn)
    sub.add_parser("merge", help="merge current records into the data file").set_defaults(fn=cmd_merge)
    p = sub.add_parser("validate", help="validate records or data files")
    p.add_argument("files", nargs="*")
    p.add_argument("--tolerant", action="store_true",
                   help="validate the way a consumer would (unknown properties/values allowed)")
    p.set_defaults(fn=cmd_validate)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
