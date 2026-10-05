"""Unit tests that need no Docker: python -m unittest discover generator/tests"""
import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gen  # noqa: E402
from normalize import _arm, _decode_arch, _mips, _riscv, _time64, normalize  # noqa: E402


class Flags(unittest.TestCase):
    def test_mips_r1_legacy_nan_fpxx(self):
        fam, abi, fl = _mips(0x50001007, "FP ABI: Hard float (32-bit CPU, Any FPU)")
        self.assertEqual((fam, fl), ("mips", "hard"))
        self.assertEqual(abi, {"isa_level": "mips32", "nan": "legacy", "fp_abi": "xx", "mips16": False, "micromips": False})

    def test_mips_r2_nan2008_soft(self):
        _, abi, fl = _mips(0x70001407, "FP ABI: Soft float")
        self.assertEqual((abi["isa_level"], abi["nan"], abi["fp_abi"], fl), ("mips32r2", "2008", "soft", "soft"))

    def test_arm_hard_float(self):
        self.assertEqual(_arm(0x05000400, "Tag_ABI_VFP_args: VFP registers"),
                         ("arm", {"eabi": 5, "vfp_args": "vfp"}, "hard"))

    def test_arm_soft_and_softfp(self):
        self.assertEqual(_arm(0x05000200, 'Tag_CPU_name: "7-A"')[2], "soft")
        self.assertEqual(_arm(0x05000200, "Tag_FP_arch: VFPv3")[2], "softfp")

    def test_riscv_lp64d_rvc(self):
        fam, abi, fl = _riscv(0x5, '  Tag_RISCV_arch: "rv64i2p1_m2p0_a2p1_f2p2_d2p2_c2p0"')
        self.assertEqual((fam, fl, abi["float_abi"], abi["rvc"], abi["rve"]), ("riscv", "hard", "double", True, False))
        self.assertTrue(abi["isa"].startswith("rv64i"))
        self.assertEqual(_riscv(0x0, "")[2], "soft")

    def test_dispatch_and_unknown_machines(self):
        self.assertEqual(_decode_arch("AArch64", 0, "")[:3:2], ("aarch64", "hard"))
        self.assertEqual(_decode_arch("ARM", 0x05000400, "")[0], "arm")
        self.assertEqual(_decode_arch("Advanced Micro Devices X86-64", 0, "")[0], "x86_64")
        self.assertEqual(_decode_arch("PowerPC64", 0x2, "Tag_GNU_Power_ABI_FP: Hard float")[1]["elf_abi"], "v2")
        # a machine nobody has written a decoder for still yields a usable record
        self.assertEqual(_decode_arch("LoongArch", 0x43, ""), ("loongarch", {}, "unknown"))
        self.assertEqual(_decode_arch("Renesas / SuperH SH", 0, "")[0], "sh")
        self.assertEqual(_decode_arch("Some Future CPU", 0, "")[0], "some")

    def test_float_abi_from_compiler_defaults(self):
        # ELF output says nothing; gcc -Q --help=target does
        sparc = "  -mhard-float                \t\t[enabled]\n  -msoft-float                \t\t[disabled]\n"
        self.assertEqual(_decode_arch("Sparc v9", 0x2, "", True, sparc)[2], "hard")
        m68k = "  -mhard-float                \t\t[disabled]\n  -msoft-float                \t\t[enabled]\n"
        self.assertEqual(_decode_arch("MC68000", 0, "", False, m68k)[2], "soft")
        csky = "  -mfloat-abi=                \t\tsoft\n  -mfloat-abi=v2              \t\t-mfloat-abi=hard\n"
        self.assertEqual(_decode_arch("CSKY", 0x21000008, "", False, csky)[2], "soft")
        ppc = "  -mhard-float                \t\t[enabled]\n"
        self.assertEqual(_decode_arch("PowerPC", 0, "", False, ppc)[2], "hard")
        self.assertEqual(_decode_arch("", 0, "", False, m68k)[:3:2], ("unknown", "soft"))    # bFLT: no ELF header

    def test_float_abi_without_compiler_defaults(self):
        self.assertEqual(_decode_arch("Xilinx MicroBlaze", 0, "")[2], "soft")       # one ABI: floats in GPRs
        self.assertEqual(_decode_arch("Xilinx MicroBlaze", 0, "", False, "  -mhard-float\t\t[enabled]")[2], "soft")
        self.assertEqual(_decode_arch("Sparc", 0, "")[2], "hard")
        self.assertEqual(_decode_arch("PowerPC64", 0x1, "", True)[2], "hard")
        self.assertEqual(_decode_arch("PowerPC", 0, "")[2], "unknown")             # 32-bit: could be either
        self.assertEqual(_decode_arch("Renesas / SuperH SH", 0x2, "", False, "", "sh4-buildroot-linux-gnu")[2], "hard")
        self.assertEqual(_decode_arch("Renesas / SuperH SH", 0x2, "", False, "", "sh2-buildroot-linux-gnu")[2], "soft")
        self.assertEqual(_decode_arch("MC68000", 0, "", False, "", "m68k-buildroot-linux-gnu")[2], "unknown")
        self.assertEqual(_decode_arch("MC68000", 0x8, "")[2], "soft")             # ColdFire ISA, no FPU flag
        self.assertEqual(_decode_arch("MC68000", 0x48, "")[2], "hard")

    def test_families_cover_bootlin_and_uname_names(self):
        from normalize import machine_family
        for name, fam in [("MC68000", "m68k"), ("Xilinx MicroBlaze", "microblaze"), ("microblazeel", "microblaze"),
                          ("Altera Nios II", "nios2"), ("OpenRISC 1000", "openrisc"), ("IBM S/390", "s390"),
                          ("s390x", "s390"), ("sh4", "sh"), ("Sparc v9", "sparc"), ("sparc64", "sparc"),
                          ("Tensilica Xtensa Processor", "xtensa"), ("ARCv2", "arc"), ("aarch64", "aarch64"),
                          ("armv7l", "arm"), ("i686", "x86"), ("x86_64", "x86_64"), ("ppc64le", "power"),
                          ("Analog Devices Blackfin", "blackfin"), ("C-SKY", "csky"),
                          ("Tilera TILE-Gx multicore architecture family", "tilegx"), ("tilegx", "tilegx"),
                          ("Tilera TILEPro multicore architecture family", "tilepro"), ("tilepro", "tilepro"),
                          ("Intel IA-64", "ia64"), ("ia64", "ia64"), ("Alpha", "alpha"), ("alpha", "alpha"),
                          ("HPPA", "parisc"), ("parisc", "parisc"), ("parisc64", "parisc")]:
            self.assertEqual(machine_family(name), fam, name)


class Time64(unittest.TestCase):
    # These mirror a disassembly of static select()/clock_gettime() programs:
    # uClibc-ng 1.0.59 emits only syscalls 4403/4413, uClibc-ng 1.0.26 only 4142/4263,
    # musl 1.2.6 and glibc 2.44 emit both.
    def test_uclibc_time64_is_required(self):
        self.assertEqual(_time64("uclibc", False, 64, "1.0.59", "#define __UCLIBC_USE_TIME64__ 1\n", ""), "required")

    def test_old_uclibc_has_none(self):
        self.assertEqual(_time64("uclibc", False, 32, "1.0.26", "#undef __UCLIBC_USE_TIME64__\n", ""), "none")

    def test_musl_falls_back(self):
        self.assertEqual(_time64("musl", False, 64, "1.2.6", "", ""), "fallback")

    def test_glibc(self):
        self.assertEqual(_time64("glibc", False, 32, "2.44", "", "3.2.0"), "fallback")
        self.assertEqual(_time64("glibc", False, 64, "2.44", "", "5.1.0"), "required")
        self.assertEqual(_time64("glibc", False, 32, "2.28", "", "3.2.0"), "none")

    def test_64bit_abi(self):
        self.assertEqual(_time64("musl", True, 64, "1.2.6", "", ""), "none")


class Normalize(unittest.TestCase):
    RAW = {
        "tc_id": "t", "triple": "mips-buildroot-linux-uclibc", "gcc_version": "15.3.0",
        "dyn.h": "  Class: ELF32\n  Data: 2's complement, big endian\n  Type: DYN (Position-Independent Executable file)\n"
                 "  Machine: MIPS R3000\n  Flags: 0x50001007, noreorder, pic, cpic, o32, mips32\n",
        "dyn.A": "ISA: MIPS32\nFP ABI: Hard float (32-bit CPU, Any FPU)\n",
        "dyn.l": "      [Requesting program interpreter: /lib/ld-uClibc.so.0]\n",
        "dyn.d": " 0x00000001 (NEEDED)  Shared library: [libc.so.0]\n 0x00000004 (HASH) 0x2a0\n",
        "link_dyn.rc": "0", "link_static.rc": "0", "time_t.8.rc": "0", "time_t.4.rc": "1",
        "ldso_soname": "ld-uClibc.so.1", "libc_soname": "libc.so.0",
        "uclibc_config": "#define __UCLIBC_MAJOR__ 1\n#define __UCLIBC_MINOR__ 0\n#define __UCLIBC_SUBLEVEL__ 59\n#define __UCLIBC_USE_TIME64__ 1\n",
        "features_h": "#define\t__GLIBC__\t2\n#define\t__GLIBC_MINOR__\t2\n",   # uClibc fakes glibc 2.2
        "linux_version_h": "#define LINUX_VERSION_CODE 330495\n",
        "corpus.threads.link.rc": "0",
        "corpus.threads.d": " (NEEDED) Shared library: [libc.so.0]\n (NEEDED) Shared library: [ld-uClibc.so.1]\n",
        "corpus.cxx.link.rc": "0", "corpus.cxx.d": " (NEEDED) Shared library: [libstdc++.so.6]\n", "cxx": "/x/g++",
        "sysroot_sonames": "libc.so.0\nlibc.so.0\nld-uClibc.so.1\n",
        "gcc_target": "  -march=ISA                  \t\tmips32\n",
    }

    def test_uclibc_2026_record(self):
        r = normalize(self.RAW, "t")
        self.assertEqual(gen.record_errors({**r, "provenance": {
            "dockerfile": "toolchains/t/Dockerfile", "dockerfile_sha256": "0" * 64,
            "image_digest": "sha256:" + "0" * 64, "toolchain_sha256": "0" * 64, "probe_version": "0" * 64}}), [])
        self.assertEqual(r["libc"], {"kind": "uclibc", "soname": "libc.so.0", "version": "1.0.59"})
        self.assertEqual(r["time"], {"time_t_bits": 64, "time64_syscalls": "required"})
        self.assertEqual(r["kernel"], {"headers": "5.10.255", "min": ""})      # code caps sublevel at 255
        self.assertEqual(r["needed_corpus"], ["ld-uClibc.so.1", "libc.so.0"])   # C++ runtime excluded
        self.assertEqual(r["sysroot_sonames"], ["ld-uClibc.so.1", "libc.so.0"])
        self.assertEqual((r["hash_style"], r["march"], r["float_abi"], r["pie_default"]), ("sysv", "mips32", "hard", True))
        self.assertNotIn("glibc", r)

    def test_float_abi_from_the_fpabi_program(self):
        # PowerPC hello has no Tag_GNU_Power_ABI_FP; the fpabi program, which passes floats, does
        hdr = Normalize.RAW["dyn.h"].replace("MIPS R3000", "PowerPC").replace("0x50001007, noreorder, pic, cpic, o32, mips32", "0x0")
        raw = dict(Normalize.RAW, **{"dyn.h": hdr, "dyn.A": "", "corpus.fpabi.link.rc": "0",
                                     "corpus.fpabi.d": " (NEEDED) Shared library: [libm.so.6]\n",
                                     "corpus.fpabi.A": "Attribute Section: gnu\n  Tag_GNU_Power_ABI_FP: Soft float\n"})
        r = normalize(raw, "t")
        self.assertEqual((r["float_abi"], r["arch"]["abi"]["fp"]), ("soft", "soft float"))
        self.assertNotIn("libm.so.6", r["needed_corpus"])        # fpabi only probes the float ABI

    def test_nothing_compiled_is_marked_compile_failed(self):
        self.assertEqual(normalize(self.RAW, "t")["probe"], {"status": "ok", "error": ""})
        raw = dict(self.RAW, **{k + ".rc": "1" for k in ("link_dyn", "link_static", "time_t.4", "time_t.8")},
                   **{"link_dyn.err": "\ngcc: error trying to exec 'cc1': execvp: No such file or directory\n"})
        self.assertEqual(normalize(raw, "t")["probe"],
                         {"status": "compile_failed", "error": "gcc: error trying to exec 'cc1': execvp: No such file or directory"})


class Binary(unittest.TestCase):
    # bFLT header: magic, rev, entry, data_start, data_end, bss_end, stack_size, reloc_start, reloc_count, flags
    BFLT = b"bFLT" + (4).to_bytes(4, "big") + bytes(28) + (0x01 | 0x02 | 0x04).to_bytes(4, "big") + bytes(24)

    def test_bflt_header_is_decoded(self):
        raw = dict(Normalize.RAW, **{"dyn.magic": " ".join(f"{c:02x}" for c in self.BFLT), "dyn.h.rc": "1"})
        self.assertEqual(normalize(raw, "t")["binary"],
                         {"format": "bflt", "bflt": {"version": 4, "flags": ["ram", "gotpic", "gzip"]}})

    def test_elf_without_magic_capture_is_recognised_by_readelf(self):
        self.assertEqual(normalize(dict(Normalize.RAW, **{"dyn.h.rc": "0"}), "t")["binary"], {"format": "elf"})

    def test_linked_but_unreadable_without_magic_is_unknown(self):
        self.assertEqual(normalize(dict(Normalize.RAW, **{"dyn.h.rc": "1"}), "t")["binary"], {"format": "unknown"})

    def test_flat_uclibc_without_magic_is_bflt_and_names_the_libc(self):
        raw = {"link_dyn.rc": "0", "link_static.rc": "0", "dyn.h.rc": "1",
               "uclibc_config": "#define __UCLIBC_MAJOR__ 1\n#define __UCLIBC_MINOR__ 0\n#define __UCLIBC_SUBLEVEL__ 28\n"
                                "#define __UCLIBC_FORMAT_FLAT__ 1\n#undef __UCLIBC_FORMAT_SHARED_FLAT__\n"}
        r = normalize(raw, "t")
        self.assertEqual(r["binary"], {"format": "bflt"})
        self.assertEqual((r["libc"]["kind"], r["libc"]["version"]), ("uclibc", "1.0.28"))


class March(unittest.TestCase):
    def test_empty_default_march_does_not_swallow_the_next_option(self):
        # AArch64 gcc prints an empty -march default
        raw = dict(Normalize.RAW, gcc_target="  -march=ARCH  \t\t\n  -mbig-endian  \t\t[disabled]\n")
        self.assertEqual(normalize(raw, "t")["march"], "")


class Registry(unittest.TestCase):
    def test_record_needs_registry_image_when_registry_set(self):
        import os
        tc = gen.toolchain_ids()[0]
        rec = {"provenance": {"dockerfile_sha256": gen.dockerfile_sha256(tc), "probe_version": "pv",
                              "normalize_version": "nv", "image_digest": "sha256:" + "0" * 64}}
        old = os.environ.pop("TCFP_REGISTRY", None)
        try:
            self.assertTrue(gen.is_current(rec, tc, "pv", "nv"))
            self.assertFalse(gen.is_current(rec, tc, "pv", "nv2"))
            self.assertTrue(gen.probe_current(rec, tc, "pv"))          # normalizer change needs no probe
            self.assertEqual(gen.stale_reason(rec, tc, "pv", "nv2"), "normalizer changed")
            os.environ["TCFP_REGISTRY"] = "ghcr.io/o/r"
            self.assertFalse(gen.is_current(rec, tc, "pv", "nv"))
            self.assertEqual(gen.stale_reason(rec, tc, "pv", "nv"), "image not in ghcr.io/o/r")
            rec["provenance"]["image_digest"] = "ghcr.io/o/r@sha256:" + "0" * 64
            self.assertTrue(gen.is_current(rec, tc, "pv", "nv"))
            self.assertEqual(gen.image_tag(tc), f"ghcr.io/o/r:{tc}-{gen.dockerfile_sha256(tc)[:12]}")
        finally:
            os.environ.pop("TCFP_REGISTRY", None)
            if old is not None:
                os.environ["TCFP_REGISTRY"] = old


class Compatibility(unittest.TestCase):
    """The published schema must accept data from a later 2.x generator."""

    def rec(self):
        import json
        return json.loads(gen.DATA.read_text())["toolchains"][0]

    def test_current_data_is_strictly_valid(self):
        import json
        self.assertEqual(gen.data_errors(json.loads(gen.DATA.read_text())), [])

    def test_unknown_property_is_tolerated_but_not_by_the_generator(self):
        r = dict(self.rec(), some_future_field={"x": 1})
        self.assertEqual(gen.record_errors(r, strict=False), [])
        self.assertTrue(any("some_future_field" in e for e in gen.record_errors(r, strict=True)))

    def test_new_open_enum_value_is_tolerated_but_not_by_the_generator(self):
        r = self.rec()
        r = dict(r, libc=dict(r["libc"], kind="bionic"))
        self.assertEqual(gen.record_errors(r, strict=False), [])
        self.assertTrue(any("x-known-values" in e for e in gen.record_errors(r, strict=True)))

    def test_closed_fields_still_enforced(self):
        r = self.rec()
        r = dict(r, elf=dict(r["elf"], endian="middle"))
        self.assertTrue(gen.record_errors(r, strict=False))


class Lint(unittest.TestCase):
    def lint_text(self, text):
        d = gen.TOOLCHAINS / "zz-lint-test"
        d.mkdir(exist_ok=True)
        try:
            (d / "Dockerfile").write_text(text, newline="\n")
            return gen.lint("zz-lint-test")[0]
        finally:
            (d / "Dockerfile").unlink()
            d.rmdir()

    GOOD = ("FROM alpine@sha256:" + "a" * 64 + " AS f\n"
            "ADD --checksum=sha256:" + "b" * 64 + " https://x/tc.tar.xz /tc.tar\n"
            "FROM debian@sha256:" + "c" * 64 + "\nCOPY --from=f /tc /opt/tc\n"
            "ENV TC_ID=zz-lint-test CC=/opt/tc/bin/x-gcc\n"
            'LABEL org.opencontainers.image.title="zz-lint-test" io.tcfp.tc_id="zz-lint-test" '
            'io.tcfp.toolchain.url="https://x/tc.tar.xz" io.tcfp.toolchain.sha256="' + "b" * 64 + '"\n')

    def test_pinned_passes(self):
        self.assertEqual(self.lint_text(self.GOOD), [])

    def test_unpinned_refused(self):
        errs = self.lint_text(self.GOOD.replace("debian@sha256:" + "c" * 64, "debian:stable")
                              + "RUN apt-get install -y make\nCOPY local.sh /\n")
        text = "\n".join(errs)
        self.assertIn("not pinned", text)
        self.assertIn("apt-get", text)
        self.assertIn("build context", text)

    def test_labels_must_match_the_toolchain(self):
        text = gen.dockerfile_text("zz-lint-test", "c", "https://x/tc.tar.xz", "d" * 64, "x-gcc", "xz")
        self.assertIn('io.tcfp.toolchain.url="https://x/tc.tar.xz"', text)
        errs = self.lint_text(text.replace('io.tcfp.toolchain.sha256="' + "d" * 64, 'io.tcfp.toolchain.sha256="' + "e" * 64))
        self.assertTrue(any("io.tcfp.toolchain.sha256" in e for e in errs))

    def test_scaffold_template_lints_clean(self):
        text = gen.dockerfile_text("zz-lint-test", "c", "https://x/tc.tar.xz", "d" * 64, "x-gcc", "xz")
        self.assertEqual(self.lint_text(text), [])

    def test_decompression_flag_is_explicit(self):
        text = gen.dockerfile_text("zz-lint-test", "c", "https://x/tc.tar.bz2", "d" * 64, "x-gcc", "bz2")
        self.assertIn("tar -xjf /tc.tar", text)

    def test_toolchain_is_the_tc_tar_download(self):
        extra = "ADD --checksum=sha256:" + "e" * 64 + " https://x/make.deb /debs/\n"
        self.assertEqual(self.lint_text(self.GOOD + extra), [])
        errs = self.lint_text(self.GOOD.replace(" /tc.tar", " /other.tar"))
        self.assertTrue(any("exactly one checksummed toolchain" in e for e in errs))

    SOURCES = [("https://x/binutils-1.tar.xz", "1" * 64), ("https://x/gcc-1.tar.xz", "2" * 64)]

    def test_source_build_template_lints_clean(self):
        text = gen.dockerfile_source_text("zz-lint-test", "c", "x-linux-gnu", "x", "x-linux-gnu-gcc", self.SOURCES)
        self.assertEqual(self.lint_text(text), [])

    def test_source_build_heredoc_is_one_instruction(self):
        text = gen.dockerfile_source_text("zz-lint-test", "c", "x-linux-gnu", "x", "x-linux-gnu-gcc", self.SOURCES)
        runs = [args for _, kw, args in gen.instructions(text) if kw == "RUN"]
        self.assertTrue(any("step gcc-final gcc3" in r for r in runs))
        self.assertFalse(any(kw == "STEP" for _, kw, _ in gen.instructions(text)))

    def test_source_build_installs_rsync_only_for_linux_5_3_headers(self):
        def text(linux):
            return gen.dockerfile_source_text("zz-lint-test", "c", "x-linux-gnu", "x", "x-linux-gnu-gcc",
                                              self.SOURCES + [(f"https://x/{linux}.tar.xz", "3" * 64)])
        self.assertNotIn("rsync", text("linux-4.16.18"))
        self.assertNotIn("rsync", text("linux-5.2.21"))
        self.assertIn("python3 rsync", text("linux-5.3"))
        self.assertIn("python3 rsync", text("linux-6.6.158"))
        self.assertEqual(self.lint_text(text("linux-6.6.158")), [])

    def test_source_build_glibc_2_29_skips_the_host_cxx(self):
        def text(glibc):
            return gen.dockerfile_source_text("zz-lint-test", "c", "x-linux-gnu", "x", "x-linux-gnu-gcc",
                                              self.SOURCES + [(f"https://x/{glibc}.tar.xz", "4" * 64)])
        self.assertNotIn("libc_cv_cxx_link_ok", text("glibc-2.27"))
        self.assertNotIn("libc_cv_cxx_link_ok", text("glibc-2.28"))
        self.assertIn("LINUX_ARCH=x libc_cv_cxx_link_ok=no\n", text("glibc-2.29"))
        self.assertIn("LINUX_ARCH=x libc_cv_cxx_link_ok=no\n", text("glibc-2.39"))
        self.assertEqual(self.lint_text(text("glibc-2.39")), [])

    def test_source_build_may_not_fetch_outside_the_snapshot(self):
        text = gen.dockerfile_source_text("zz-lint-test", "c", "x-linux-gnu", "x", "x-linux-gnu-gcc", self.SOURCES)
        errs = self.lint_text(text.replace("snapshot.debian.org/archive/debian/", "deb.debian.org/debian/", 1))
        self.assertTrue(any("deb.debian.org" in e for e in errs))
        errs = self.lint_text(text.replace("RUN <<'BUILD'", "RUN curl -O https://x/y\nRUN <<'BUILD'", 1))
        self.assertTrue(any("curl" in e for e in errs))

    def test_template_links_the_build_prefix(self):
        text = gen.dockerfile_text("zz-lint-test", "c", "https://x/aarch64--glibc--stable-2018.02-1.tar.bz2",
                                   "d" * 64, "x-gcc", "bz2")
        self.assertIn("RUN ln -s /opt/tc /opt/aarch64--glibc--stable-2018.02-1\n", text)

    def test_regen_keeps_pins_and_adds_only_the_template_change(self):
        url = "https://x/tc--x--stable-1.tar.xz"
        old = gen.dockerfile_text("zz-lint-test", "Vendor tc", url, "d" * 64, "x-gcc", "xz")
        old = old.replace(f"RUN ln -s /opt/tc {gen.build_prefix(url)}\n", "")   # a Dockerfile from before the link
        d = gen.TOOLCHAINS / "zz-lint-test"
        d.mkdir(exist_ok=True)
        try:
            (d / "Dockerfile").write_text(old, newline="\n")
            self.assertTrue(gen.regen("zz-lint-test"))
            self.assertEqual((d / "Dockerfile").read_text(),
                             gen.dockerfile_text("zz-lint-test", "Vendor tc", url, "d" * 64, "x-gcc", "xz"))
            self.assertFalse(gen.regen("zz-lint-test"))                               # idempotent
        finally:
            (d / "Dockerfile").unlink()
            d.rmdir()

    def test_prebuilt_still_may_not_use_apt(self):
        errs = self.lint_text(self.GOOD + "RUN apt-get install -y make\n")
        self.assertTrue(any("apt-get" in e for e in errs))

    def test_unchecksummed_download_refused(self):
        errs = self.lint_text(self.GOOD.replace("--checksum=sha256:" + "b" * 64 + " ", ""))
        self.assertTrue(any("without --checksum" in e for e in errs))


class Tarball(unittest.TestCase):
    def test_compression_is_detected_from_the_content(self):
        for comp in ("gz", "bz2", "xz"):
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "tc.tar"                   # no extension to go by
                with tarfile.open(path, f"w:{comp}") as t:
                    info = tarfile.TarInfo("tc/bin/x-linux-gcc")
                    t.addfile(info, io.BytesIO())
                _, got, tops, gccs = gen.inspect_tarball(path.as_uri())
                self.assertEqual((got, tops, gccs), (comp, {"tc"}, ["x-linux-gcc"]))


if __name__ == "__main__":
    unittest.main()
