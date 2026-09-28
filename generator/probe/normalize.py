"""Normalize the raw files probe.sh collects into one schema record.

This lives in probe/ (not gen.py) on purpose: probe_version is the hash of
this directory, so fixing an extraction bug here invalidates exactly the
records it affects.
"""
import re


def _field(text, label):
    m = re.search(rf"^\s*{re.escape(label)}:\s*(.*?)\s*$", text or "", re.M)
    return m.group(1) if m else ""


def normalize(raw, tc_id):
    """raw: dict of probe file name -> text. Returns a schema record (no provenance)."""
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
