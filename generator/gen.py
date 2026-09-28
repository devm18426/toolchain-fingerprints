#!/usr/bin/env python3
"""Toolchain fingerprint generator.

Each step is its own command, so a failure points at one step:

    gen.py status                 what is up to date, stale, or invalid (read-only)
    gen.py lint     [TC_ID...]    check that every Dockerfile input is pinned
    gen.py build    TC_ID         docker build toolchains/TC_ID (RUN steps have no network)
    gen.py probe    TC_ID         run the probe in the built image -> .cache/records/TC_ID.json
    gen.py merge                  merge current records into data/fingerprints.json
    gen.py validate [FILE...]     validate records or a data file against the schema

A record is computed once. It is current exactly as long as its provenance
(Dockerfile hash + probe version) matches the repo; there is no periodic recheck.

Only the Python standard library is used. The generator knows nothing about how
the data is displayed or matched: its only output is data/fingerprints.json,
which must validate against schema/fingerprint.schema.json.
"""
import argparse
import datetime
import hashlib
import io
import json
import re
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema" / "fingerprint.schema.json"
PROBE_DIR = ROOT / "generator" / "probe"
TOOLCHAINS = ROOT / "toolchains"
CACHE = ROOT / ".cache" / "records"
DATA = ROOT / "data" / "fingerprints.json"

sys.path.insert(0, str(PROBE_DIR))
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


def validate(inst, sch, root=None, path="$"):
    """Return a list of error strings; empty means valid."""
    root = root if root is not None else sch
    errs = []
    if "$ref" in sch:
        node = root
        for part in sch["$ref"].lstrip("#/").split("/"):
            node = node[part]
        errs += validate(inst, node, root, path)
    if "type" in sch:
        types = sch["type"] if isinstance(sch["type"], list) else [sch["type"]]
        if not any(_is_type(inst, t) for t in types):
            return errs + [f"{path}: expected {'/'.join(types)}, got {type(inst).__name__}"]
    if "enum" in sch and inst not in sch["enum"]:
        errs.append(f"{path}: {inst!r} not in {sch['enum']}")
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
        extra = sch.get("additionalProperties", True)
        for k, v in inst.items():
            if k in props:
                errs += validate(v, props[k], root, f"{path}.{k}")
            elif extra is False:
                errs.append(f"{path}: unexpected property '{k}'")
            elif isinstance(extra, dict):
                errs += validate(v, extra, root, f"{path}.{k}")
    if isinstance(inst, list) and "items" in sch:
        for i, v in enumerate(inst):
            errs += validate(v, sch["items"], root, f"{path}[{i}]")
    return errs


def record_errors(rec):
    return validate(rec, {"$ref": "#/$defs/record"}, schema())


def data_errors(doc):
    errs = validate(doc, schema())
    ids = [r.get("tc_id") for r in doc.get("toolchains", []) if isinstance(r, dict)]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        errs.append(f"$.toolchains: duplicate tc_id {sorted(dup)}")
    return errs


# --------------------------------------------------------------------------
# Provenance inputs
# --------------------------------------------------------------------------
def probe_version():
    """sha256 over every file in generator/probe/ (path + LF-normalized content)."""
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


def image_tag(tc_id):
    return f"tcfp/{tc_id}:{dockerfile_sha256(tc_id)[:12]}"


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
    errs, facts, stages, url_sums, env = [], {}, set(), [], {}
    for n, kw, args in instructions(path.read_text()):
        where = f"{path.relative_to(ROOT).as_posix()}:{n}"
        flags, toks = _flags(args)
        if kw == "FROM":
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
                    else:
                        url_sums.append(m.group(1))
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
    if len(url_sums) != 1:
        errs.append(f"{path.relative_to(ROOT).as_posix()}: expected exactly one checksummed toolchain "
                    f"download (ADD --checksum=sha256:... URL), found {len(url_sums)}")
    else:
        facts["toolchain_sha256"] = url_sums[0]
    if env.get("TC_ID") != tc_id:
        errs.append(f"{path.relative_to(ROOT).as_posix()}: ENV TC_ID must be '{tc_id}' (the directory name), "
                    f"found {env.get('TC_ID')!r}")
    if not env.get("CC"):
        errs.append(f"{path.relative_to(ROOT).as_posix()}: ENV CC must name the cross gcc")
    return errs, facts


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
    cmd = ["docker", "run", "--rm", "-i", "--network=none", "--entrypoint", "/bin/sh", image, "-c",
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
# --------------------------------------------------------------------------
def load_data():
    return {r["tc_id"]: r for r in json.loads(DATA.read_text())["toolchains"]} if DATA.exists() else {}


def load_cache():
    out = {}
    for f in sorted(CACHE.glob("*.json")):
        r = json.loads(f.read_text())
        out[r["tc_id"]] = r
    return out


def is_current(rec, tc_id, pv):
    p = (rec or {}).get("provenance", {})
    return p.get("dockerfile_sha256") == dockerfile_sha256(tc_id) and p.get("probe_version") == pv


def stale_reason(rec, tc_id, pv):
    if not rec:
        return "never probed"
    p = rec.get("provenance", {})
    why = []
    if p.get("dockerfile_sha256") != dockerfile_sha256(tc_id):
        why.append("Dockerfile changed")
    if p.get("probe_version") != pv:
        why.append("probe changed")
    return ", ".join(why)


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
    cmd = ["docker", "build", "--network=none", "-t", tag, str(TOOLCHAINS / a.tc_id)]
    print("+ " + " ".join(cmd), flush=True)
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        die(f"docker build failed for {a.tc_id} (exit {rc})")
    print(f"built {tag} = {image_id(tag)}")


def cmd_probe(a):
    need_toolchain(a.tc_id)
    facts = need_lint(a.tc_id)
    tag = image_tag(a.tc_id)
    iid = image_id(tag)
    if not iid:
        die(f"image {tag} not built; run: gen.py build {a.tc_id}")
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
    }
    errs = record_errors(rec)
    if errs:
        die("record does not validate:\n  " + "\n  ".join(errs))
    out = CACHE / f"{a.tc_id}.json"
    write_json(out, rec)
    print(f"wrote {out.relative_to(ROOT).as_posix()}")


def cmd_status(a):
    pv, data, cache = probe_version(), load_data(), load_cache()
    todo = 0
    for tc in toolchain_ids():
        errs, _ = lint(tc)
        built = "built" if image_id(image_tag(tc)) else "not built"
        if errs:
            state, todo = f"REFUSED: {len(errs)} unpinned input(s), see gen.py lint {tc}", todo + 1
        elif is_current(data.get(tc), tc, pv):
            state = "up to date"
        elif is_current(cache.get(tc), tc, pv):
            state, todo = "probed, run gen.py merge", todo + 1
        else:
            state, todo = f"stale ({stale_reason(data.get(tc), tc, pv)}), image {built}", todo + 1
        print(f"{tc:28} {state}")
    for tc in sorted(set(data) - set(toolchain_ids())):
        print(f"{tc:28} orphan record (no Dockerfile); gen.py merge drops it")
        todo += 1
    sys.exit(1 if todo else 0)


def cmd_merge(a):
    pv, data, cache, ids = probe_version(), load_data(), load_cache(), set(toolchain_ids())
    recs, stale = {}, []
    for tc in sorted(ids):
        if is_current(cache.get(tc), tc, pv):
            recs[tc] = cache[tc]
        elif tc in data:
            recs[tc] = data[tc]
            if not is_current(data[tc], tc, pv):
                stale.append(f"{tc} ({stale_reason(data[tc], tc, pv)})")
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
    write_json(DATA, doc)
    print(f"wrote {DATA.relative_to(ROOT).as_posix()} ({len(recs)} toolchains)")
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
        errs = data_errors(doc) if "schema_version" in doc else record_errors(doc)
        for e in errs:
            print(f"{f}: {e}")
        if not errs:
            print(f"{f}: ok")
        bad += bool(errs)
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show what is up to date").set_defaults(fn=cmd_status)
    p = sub.add_parser("lint", help="check Dockerfiles are fully pinned")
    p.add_argument("tc_ids", nargs="*")
    p.set_defaults(fn=cmd_lint)
    for name, fn, h in (("build", cmd_build, "build one toolchain image"),
                        ("probe", cmd_probe, "probe one built toolchain image")):
        p = sub.add_parser(name, help=h)
        p.add_argument("tc_id")
        p.set_defaults(fn=fn)
    sub.add_parser("merge", help="merge current records into the data file").set_defaults(fn=cmd_merge)
    p = sub.add_parser("validate", help="validate records or data files")
    p.add_argument("files", nargs="*")
    p.set_defaults(fn=cmd_validate)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
