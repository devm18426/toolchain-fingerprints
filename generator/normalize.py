"""Normalize the raw files probe.sh collects into one schema record.

Records are re-derived from the stored raw output (data/raw/) whenever this
file changes (provenance.normalize_version), so improving extraction or adding
an architecture never needs a Docker build or a new probe run.

Adding an architecture: write one decoder below and register it in DECODERS
(and FAMILIES, if its name is new). It returns (family, abi, float_abi); abi is a flat dict of scalars that lands in
the record's open-ended `arch.abi` object, so no schema or page change is needed.
"""
import re


def _field(text, label):
    m = re.search(rf"^\s*{re.escape(label)}:\s*(.*?)\s*$", text or "", re.M)
    return m.group(1) if m else ""


def _vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def _max_version(versions):
    return max(versions, key=_vkey) if versions else ""


def _summary_version(summary, pkg):
    m = re.search(rf'^"{re.escape(pkg)}","([0-9][0-9.]*)', summary, re.M)
    return m.group(1).rstrip(".") if m else ""


def _needed(dyn):
    return re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", dyn)


# --- per-architecture ABI decoders -------------------------------------------
# Each takes (e_flags, readelf -A text) and returns (family, abi, float_abi).
_MIPS_ARCH = {0x0: "mips1", 0x1: "mips2", 0x2: "mips3", 0x3: "mips4", 0x4: "mips5",
              0x5: "mips32", 0x6: "mips64", 0x7: "mips32r2", 0x8: "mips64r2",
              0x9: "mips32r6", 0xa: "mips64r6"}
_MIPS_FP = [("soft float", "soft"), ("hard float (double precision)", "double"),
            ("hard float (single precision)", "single"), ("hard float (32-bit cpu, any fpu)", "xx"),
            ("hard float (32-bit cpu, 64-bit fpu)", "64"), ("hard float compat", "64a"),
            ("hard float (mips32r2 64-bit fpu", "old-64"), ("hard or soft float", "any")]


def _mips(flags, attrs):
    fp_text = (_field(attrs, "FP ABI") or _field(attrs, "Tag_GNU_MIPS_ABI_FP")).lower()
    fp = next((v for k, v in _MIPS_FP if fp_text.startswith(k)), "unknown")
    abi = {
        "isa_level": _MIPS_ARCH.get(flags >> 28, f"unknown-{flags >> 28:#x}"),
        "nan": "2008" if flags & 0x400 else "legacy",
        "fp_abi": fp,
        "mips16": bool(flags & 0x04000000),
        "micromips": bool(flags & 0x02000000),
    }
    return "mips", abi, {"soft": "soft", "unknown": "unknown"}.get(fp, "hard")


def _arm(flags, attrs):
    vfp = _field(attrs, "Tag_ABI_VFP_args").lower()
    vfp_args = ("vfp" if "vfp registers" in vfp else "toolchain" if "toolchain" in vfp
                else "compatible" if "compatible" in vfp else "base" if vfp else "none")
    if vfp_args == "vfp" or flags & 0x400:            # EF_ARM_ABI_FLOAT_HARD
        fl = "hard"
    else:
        fl = "softfp" if _field(attrs, "Tag_FP_arch") else "soft"
    return "arm", {"eabi": flags >> 24, "vfp_args": vfp_args}, fl


def _riscv(flags, attrs):
    # e_flags: RVC 0x1, float ABI 0x6 (0 soft, 2 single, 4 double, 6 quad), RVE 0x8, TSO 0x10
    fabi = {0x0: "soft", 0x2: "single", 0x4: "double", 0x6: "quad"}[flags & 0x6]
    abi = {"float_abi": fabi, "rvc": bool(flags & 0x1), "rve": bool(flags & 0x8), "tso": bool(flags & 0x10)}
    arch = _field(attrs, "Tag_RISCV_arch")
    if arch:
        abi["isa"] = arch.strip('"')
    return "riscv", abi, "soft" if fabi == "soft" else "hard"


def _power(flags, attrs):
    fp = _field(attrs, "Tag_GNU_Power_ABI_FP").lower()
    abi = {"fp": fp or "unspecified"}
    if flags & 0x3:                                   # EF_PPC64_ABI: 1 = ELFv1, 2 = ELFv2
        abi["elf_abi"] = f"v{flags & 0x3}"
    fl = "soft" if "soft" in fp else "hard" if "hard" in fp else "unknown"
    return "power", abi, fl


def _fixed_soft(family):
    # Tile has no FPU: floating point is always done in software, with one ABI
    return lambda flags, attrs: (family, {}, "soft")


def _fixed_hard(family):
    # AArch64 and x86 have a single hard-float procedure call standard
    return lambda flags, attrs: (family, {}, "hard")


# Machine name (readelf's Machine text or uname -m) -> family. The web page has
# the same table (site/matcher.js); keep them in sync. Names not listed become
# their first word, lowercased, on both sides.
FAMILIES = [
    (r"^(x86[-_]64|amd64|advanced micro devices x86-64)", "x86_64"),
    (r"^(i[3-6]86|x86$|intel 80386)", "x86"),
    (r"^(aarch64|arm64)", "aarch64"),
    (r"^arm", "arm"),
    (r"^mips", "mips"),
    (r"^risc-?v", "riscv"),
    (r"^(ppc|powerpc|power)", "power"),
    (r"^loongarch", "loongarch"),
    (r"^(mc68|m68k|coldfire)", "m68k"),
    (r"^(xilinx microblaze|microblaze)", "microblaze"),
    (r"^(altera nios|nios2)", "nios2"),
    (r"^(openrisc|or1k)", "openrisc"),
    (r"^(ibm s/390|s390)", "s390"),
    (r"^(renesas / superh|superh|sh\d|sh$)", "sh"),
    (r"^sparc", "sparc"),
    (r"^(tensilica xtensa|xtensa)", "xtensa"),
    (r"^(arcv2|arcompact|arc)", "arc"),
    (r"^(analog devices blackfin|blackfin|bfin)", "blackfin"),
    (r"^(c-sky|csky)", "csky"),
    (r"^(tilera tile-gx|tilegx)", "tilegx"),       # TILE-Gx and TILEPro are different ISAs
    (r"^(tilera tilepro|tilepro)", "tilepro"),
]


def machine_family(name):
    s = (name or "").strip().lower()
    for rx, fam in FAMILIES:
        if re.match(rx, s):
            return fam
    return s.split()[0] if s else "unknown"


# family -> ABI decoder. Families without one still get a full record; only
# float_abi is "unknown" and arch.abi is empty.
DECODERS = {
    "mips": _mips,
    "arm": _arm,
    "riscv": _riscv,
    "power": _power,
    "aarch64": _fixed_hard("aarch64"),
    "x86_64": _fixed_hard("x86_64"),
    "x86": _fixed_hard("x86"),
    "tilegx": _fixed_soft("tilegx"),
    "tilepro": _fixed_soft("tilepro"),
}


def _decode_arch(machine, flags, attrs):
    family = machine_family(machine)
    if family in DECODERS:
        return DECODERS[family](flags, attrs)
    return family, {}, "unknown"


# --- time64 --------------------------------------------------------------------
def _time64(kind, elf64, time_bits, libc_ver, uclibc_cfg, kmin):
    """How the libc's time calls reach the kernel.

    Checked by disassembling a static select()/clock_gettime() program for each
    MIPS toolchain: uClibc-ng with __UCLIBC_USE_TIME64__ issues only the
    *_time64 syscalls; musl >= 1.2 and glibc >= 2.32 issue both (try, then fall
    back on ENOSYS); older libcs issue only the legacy ones.
    """
    if elf64:
        return "none"                                   # 64-bit ABIs never had a 32-bit time_t
    if kind == "uclibc":
        return "required" if re.search(r"^#define __UCLIBC_USE_TIME64__ 1", uclibc_cfg, re.M) else "none"
    if kind == "musl":
        return "fallback" if time_bits == 64 else "none"
    if kind == "glibc":
        if kmin and _vkey(kmin) >= (5, 1):
            return "required"                           # __ASSUME_TIME64_SYSCALLS: no fallback
        return "fallback" if libc_ver and _vkey(libc_ver) >= (2, 32) else "none"
    return "unknown"


def normalize(raw, tc_id):
    """raw: dict of probe file name -> text. Returns a schema record (no provenance)."""
    g = lambda k: (raw.get(k) or "").strip()
    ok = lambda k: g(k + ".rc") == "0"
    hdr, attrs, prog, dyn = g("dyn.h"), g("dyn.A"), g("dyn.l"), g("dyn.d")

    cls = _field(hdr, "Class")                          # "ELF32"
    data = _field(hdr, "Data")                          # "2's complement, big endian"
    etype = _field(hdr, "Type").split(" ")[0]          # "EXEC" | "DYN"
    machine = _field(hdr, "Machine")
    m = re.match(r"(0x[0-9a-fA-F]+)", _field(hdr, "Flags"))
    flags = int(m.group(1), 16) if m else 0
    m = re.search(r"program interpreter:\s*([^\]\s]+)", prog)
    interp = m.group(1) if m else ""

    ldso, libc_soname = g("ldso_soname"), g("libc_soname")
    both = ldso + " " + interp
    # glibc loaders: ld-linux*.so.N (most arches), ld.so.1 (MIPS, PowerPC32), ld64.so.N (PowerPC64, s390x)
    kind = ("musl" if "musl" in both else "uclibc" if "uClibc" in both
            else "glibc" if re.search(r"\bld(64)?(-linux[\w.-]*)?\.so\b", both) else "unknown")
    if kind == "musl" and not libc_soname and interp:
        libc_soname = interp.rsplit("/", 1)[-1]      # musl's loader IS libc

    # libc version: headers first, the Buildroot SDK summary where headers have none (musl)
    feat, ucfg, summary = g("features_h"), g("uclibc_config"), g("summary")
    macro = lambda name, text: (re.search(rf"define\s+{name}\s+(\d+)", text) or [None, ""])[1]
    if kind == "uclibc":
        parts = [macro(f"__UCLIBC_{p}__", ucfg + "\n" + feat) for p in ("MAJOR", "MINOR", "SUBLEVEL")]
        libc_ver = ".".join(parts) if all(parts) else _summary_version(summary, "uclibc")
    elif kind == "glibc":
        parts = [macro("__GLIBC__", feat), macro("__GLIBC_MINOR__", feat)]
        libc_ver = ".".join(parts) if all(parts) else _summary_version(summary, "glibc")
    elif kind == "musl":
        libc_ver = _summary_version(summary, "musl")
    else:
        libc_ver = ""

    # kernel headers: exact version from the SDK summary, else LINUX_VERSION_CODE (sublevel caps at 255)
    kh = _summary_version(summary, "linux-headers")
    if not kh:
        m = re.search(r"LINUX_VERSION_CODE\s+(\d+)", g("linux_version_h"))
        if m:
            c = int(m.group(1))
            kh = f"{c >> 16}.{(c >> 8) & 0xff}.{c & 0xff}"
    corpus = sorted({k.split(".")[1] for k in raw if k.startswith("corpus.") and k.endswith(".link.rc")})
    notes = "\n".join(g(f"corpus.{n}.n") for n in corpus)
    m = re.search(r"OS:\s*Linux,\s*ABI:\s*([\d.]+)", notes)
    kmin = m.group(1) if m else ""

    time_bits = 64 if ok("time_t.8") else 32

    # C programs only: C++ runtimes are normally linked statically for these targets (cxx_ok covers C++)
    c_corpus = [n for n in corpus if n != "cxx"]
    needed_corpus = sorted({s for n in c_corpus for s in _needed(g(f"corpus.{n}.d"))} | set(_needed(dyn)))
    has_hash, has_gnu = bool(re.search(r"\(HASH\)", dyn)), bool(re.search(r"\(GNU_HASH\)", dyn))
    m = re.search(r"^[ \t]*-march=\S*[ \t]+(\S*)[ \t]*$", g("gcc_target"), re.M)   # empty on some arches

    family, arch_abi, float_abi = _decode_arch(machine, flags, attrs)

    # Nothing compiled: every field derived from compiler output below is a default, not a fact.
    compiled = any(ok(k) for k in ("link_dyn", "link_static", "time_t.4", "time_t.8"))
    err = next((ln.strip() for k in ("link_dyn", "time_t.8", "gcc_target")
                for ln in g(k + ".err").splitlines() if ln.strip()), "")

    rec = {
        "tc_id": tc_id,
        "triple": g("triple"),
        "gcc_version": g("gcc_version"),
        "libc": {"kind": kind, "soname": libc_soname, "version": libc_ver},
        "ldso": {"soname": ldso},
        "interp": interp,
        "elf": {
            "class": "64" if cls == "ELF64" else "32",
            "endian": "little" if "little" in data else "big",
            "machine": machine,
        },
        "isa": _field(attrs, "ISA"),
        "float_abi": float_abi,
        "pie_default": etype == "DYN",
        "needed": _needed(dyn),
        "dynamic_ok": ok("link_dyn"),
        "static_ok": ok("link_static"),
        "needed_corpus": needed_corpus,
        "sysroot_sonames": sorted(set(g("sysroot_sonames").split())),
        "cxx_ok": bool(g("cxx")) and ok("corpus.cxx.link"),
        "march": m.group(1) if m else "",
        "hash_style": "both" if has_hash and has_gnu else "gnu" if has_gnu else "sysv" if has_hash else "none",
        "time": {
            "time_t_bits": time_bits,
            "time64_syscalls": _time64(kind, cls == "ELF64", time_bits, libc_ver, ucfg, kmin),
        },
        "kernel": {"headers": kh, "min": kmin},
        "probe": {"status": "ok" if compiled else "compile_failed", "error": "" if compiled else err[:300]},
    }
    if kind == "glibc":
        req = {v for n in c_corpus for v in re.findall(r"GLIBC_(\d[\d.]*)", g(f"corpus.{n}.V"))}
        rec["glibc"] = {"requires": _max_version(req),
                        "provides": _max_version(set(re.findall(r"GLIBC_(\d[\d.]*)", g("libc_V"))))}
    rec["arch"] = {"family": family, "abi": arch_abi}
    # 2.1 fields kept for existing consumers; new consumers should read arch.abi
    if family == "mips":
        rec["mips"] = arch_abi
    if family == "arm":
        rec["arm"] = arch_abi
    rec["raw"] = {"class": cls, "data": data, "type": _field(hdr, "Type"), "flags": _field(hdr, "Flags"),
                  "fp_abi": _field(attrs, "FP ABI") or _field(attrs, "Tag_ABI_VFP_args")}
    return rec
