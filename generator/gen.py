#!/usr/bin/env python3
"""Toolchain fingerprint generator.

Each step is its own command so a failure points at one step:

    gen.py probe  <tc_id> --image IMG   run the probe in IMG, write .cache/records/<tc_id>.json
    gen.py validate [FILE...]           validate records or a data file against the schema
    gen.py merge                        merge cached records into data/fingerprints.json

Only the Python standard library is used. The generator knows nothing about how
the data is displayed or matched; its only output is data/fingerprints.json,
which must validate against schema/fingerprint.schema.json.
"""
import argparse
import datetime
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema" / "fingerprint.schema.json"
PROBE_DIR = ROOT / "generator" / "probe"
CACHE = ROOT / ".cache" / "records"
DATA = ROOT / "data" / "fingerprints.json"


def die(msg):
    print(f"gen: {msg}", file=sys.stderr)
    sys.exit(1)


def schema_version():
    """The version this generator writes: the schema file's own x-version."""
    return json.loads(SCHEMA.read_text())["x-version"]


# --------------------------------------------------------------------------
# Minimal JSON Schema validator (the subset fingerprint.schema.json uses)
# --------------------------------------------------------------------------
_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "null": type(None),
}


def _is_type(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def validate(inst, schema, root=None, path="$"):
    """Return a list of error strings; empty means valid."""
    root = root if root is not None else schema
    errs = []
    if "$ref" in schema:
        node = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        errs += validate(inst, node, root, path)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(inst, t) for t in types):
            return errs + [f"{path}: expected {'/'.join(types)}, got {type(inst).__name__}"]
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {inst!r} not in {schema['enum']}")
    if "const" in schema and inst != schema["const"]:
        errs.append(f"{path}: must be {schema['const']!r}")
    if isinstance(inst, str) and "pattern" in schema and not re.search(schema["pattern"], inst):
        errs.append(f"{path}: {inst!r} does not match {schema['pattern']}")
    if isinstance(inst, (int, float)) and not isinstance(inst, bool) and "minimum" in schema and inst < schema["minimum"]:
        errs.append(f"{path}: {inst} < minimum {schema['minimum']}")
    if isinstance(inst, dict):
        for k in schema.get("required", []):
            if k not in inst:
                errs.append(f"{path}: missing required '{k}'")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for k, v in inst.items():
            if k in props:
                errs += validate(v, props[k], root, f"{path}.{k}")
            elif extra is False:
                errs.append(f"{path}: unexpected property '{k}'")
            elif isinstance(extra, dict):
                errs += validate(v, extra, root, f"{path}.{k}")
    if isinstance(inst, list) and "items" in schema:
        for i, v in enumerate(inst):
            errs += validate(v, schema["items"], root, f"{path}[{i}]")
    return errs


def record_errors(rec):
    schema = json.loads(SCHEMA.read_text())
    return validate(rec, {"$ref": "#/$defs/record"}, schema)


def data_errors(doc):
    schema = json.loads(SCHEMA.read_text())
    errs = validate(doc, schema)
    ids = [r.get("tc_id") for r in doc.get("toolchains", []) if isinstance(r, dict)]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        errs.append(f"$.toolchains: duplicate tc_id {sorted(dup)}")
    return errs


# --------------------------------------------------------------------------
# Normalization: raw probe files -> schema record
# --------------------------------------------------------------------------
def _field(text, label):
    m = re.search(rf"^\s*{re.escape(label)}:\s*(.*?)\s*$", text or "", re.M)
    return m.group(1) if m else ""


def normalize(raw, tc_id):
    """raw: dict of probe file name -> text. Returns a schema record."""
    g = lambda k: (raw.get(k) or "").strip()
    ok = lambda k: g(k + ".rc") == "0"
    hdr, attrs, prog, dyn = g("dyn.h"), g("dyn.A"), g("dyn.l"), g("dyn.d")

    cls = _field(hdr, "Class")                          # "ELF32"
    data = _field(hdr, "Data")                          # "2's complement, big endian"
    etype = _field(hdr, "Type").split(" ")[0]          # "EXEC" | "DYN"
    m = re.search(r"program interpreter:\s*([^\]\s]+)", prog)
    interp = m.group(1) if m else ""
    needed = re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", dyn)
    fl = re.search(r"soft.?float|hard.?float|softfp", hdr + "\n" + attrs, re.I)
    fl = (fl.group(0).lower() if fl else "")
    float_abi = "softfp" if fl == "softfp" else "hard" if fl.startswith("hard") else "soft" if fl.startswith("soft") else "unknown"

    ldso, libc_soname = g("ldso_soname"), g("libc_soname")
    both = ldso + interp
    kind = ("musl" if "musl" in both else "uclibc" if "uClibc" in both
            else "glibc" if ("ld-linux" in both or "ld.so" in both) else "unknown")
    if kind == "musl" and not libc_soname and interp:
        libc_soname = interp.rsplit("/", 1)[-1]      # musl's loader IS libc

    return {
        "tc_id": tc_id,
        "triple": g("triple"),
        "gcc_version": g("gcc_version"),
        "libc": {"kind": kind, "soname": libc_soname},
        "ldso": {"soname": ldso},
        "interp": interp,
        "elf": {
            "class": "64" if cls == "ELF64" else "32",
            "endian": "little" if "little" in data else "big",
            "machine": _field(hdr, "Machine"),
        },
        "isa": _field(attrs, "ISA"),
        "float_abi": float_abi,
        "pie_default": etype == "DYN",
        "needed": needed,
        "dynamic_ok": ok("link_dyn"),
        "static_ok": ok("link_static"),
        "raw": {"class": cls, "data": data, "type": _field(hdr, "Type"), "float": fl},
    }


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def probe_tar():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for p in sorted(PROBE_DIR.rglob("*")):
            if p.is_file():
                data = p.read_bytes().replace(b"\r\n", b"\n")
                info = tarfile.TarInfo(p.relative_to(PROBE_DIR).as_posix())
                info.size, info.mode = len(data), 0o755
                t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def run_probe(image):
    """Send the probe into the container on stdin, get the raw facts back as a tar on stdout."""
    cmd = ["docker", "run", "--rm", "-i", "--network=none", "--entrypoint", "/bin/sh", image, "-c",
           "mkdir -p /probe && tar -xf - -C /probe && exec bash /probe/probe.sh"]
    p = subprocess.run(cmd, input=probe_tar(), capture_output=True)
    if p.returncode != 0:
        die(f"probe failed in {image} (exit {p.returncode}):\n{p.stderr.decode(errors='replace')}")
    raw = {}
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as t:
        for m in t.getmembers():
            if m.isfile():
                raw[m.name.lstrip("./")] = t.extractfile(m).read().decode(errors="replace")
    return raw


def cmd_probe(a):
    raw = run_probe(a.image)
    rec = normalize(raw, a.tc_id)
    errs = record_errors(rec)
    if errs:
        die("record does not validate:\n  " + "\n  ".join(errs))
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{a.tc_id}.json"
    out.write_text(json.dumps(rec, indent=2) + "\n")
    print(f"wrote {out.relative_to(ROOT)}")


def cmd_validate(a):
    files = a.files or [str(DATA)]
    bad = 0
    for f in files:
        doc = json.loads(Path(f).read_text())
        errs = data_errors(doc) if "schema_version" in doc else record_errors(doc)
        for e in errs:
            print(f"{f}: {e}")
        bad += bool(errs)
        if not errs:
            print(f"{f}: ok")
    sys.exit(1 if bad else 0)


def cmd_merge(a):
    recs = {}
    if DATA.exists():
        for r in json.loads(DATA.read_text()).get("toolchains", []):
            recs[r["tc_id"]] = r
    for f in sorted(CACHE.glob("*.json")):
        r = json.loads(f.read_text())
        errs = record_errors(r)
        if errs:
            die(f"{f.name} does not validate:\n  " + "\n  ".join(errs))
        recs[r["tc_id"]] = r
    doc = {
        "schema_version": schema_version(),
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "toolchains": [recs[k] for k in sorted(recs)],
    }
    errs = data_errors(doc)
    if errs:
        die("merged data does not validate:\n  " + "\n  ".join(errs))
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {DATA.relative_to(ROOT)} ({len(recs)} toolchains)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="probe one toolchain image")
    p.add_argument("tc_id")
    p.add_argument("--image", required=True)
    p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("validate", help="validate records or data files")
    p.add_argument("files", nargs="*")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("merge", help="merge cached records into the data file")
    p.set_defaults(fn=cmd_merge)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
