"""Unit tests that need no Docker: python -m unittest discover generator/tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gen  # noqa: E402
from normalize import _arm, _mips, _time64, normalize  # noqa: E402


class Flags(unittest.TestCase):
    def test_mips_r1_legacy_nan_fpxx(self):
        m = _mips(0x50001007, "FP ABI: Hard float (32-bit CPU, Any FPU)")
        self.assertEqual(m, {"isa_level": "mips32", "nan": "legacy", "fp_abi": "xx", "mips16": False, "micromips": False})

    def test_mips_r2_nan2008_soft(self):
        m = _mips(0x70001407, "FP ABI: Soft float")
        self.assertEqual((m["isa_level"], m["nan"], m["fp_abi"]), ("mips32r2", "2008", "soft"))

    def test_arm_hard_float(self):
        self.assertEqual(_arm(0x05000400, "Tag_ABI_VFP_args: VFP registers"), {"eabi": 5, "vfp_args": "vfp"})

    def test_arm_soft(self):
        self.assertEqual(_arm(0x05000200, "Tag_CPU_name: \"7-A\""), {"eabi": 5, "vfp_args": "none"})


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


class Registry(unittest.TestCase):
    def test_record_needs_registry_image_when_registry_set(self):
        import os
        tc = gen.toolchain_ids()[0]
        rec = {"provenance": {"dockerfile_sha256": gen.dockerfile_sha256(tc), "probe_version": "pv",
                              "image_digest": "sha256:" + "0" * 64}}
        old = os.environ.pop("TCFP_REGISTRY", None)
        try:
            self.assertTrue(gen.is_current(rec, tc, "pv"))
            os.environ["TCFP_REGISTRY"] = "ghcr.io/o/r"
            self.assertFalse(gen.is_current(rec, tc, "pv"))
            self.assertEqual(gen.stale_reason(rec, tc, "pv"), "image not in ghcr.io/o/r")
            rec["provenance"]["image_digest"] = "ghcr.io/o/r@sha256:" + "0" * 64
            self.assertTrue(gen.is_current(rec, tc, "pv"))
            self.assertEqual(gen.image_tag(tc), f"ghcr.io/o/r:{tc}-{gen.dockerfile_sha256(tc)[:12]}")
        finally:
            os.environ.pop("TCFP_REGISTRY", None)
            if old is not None:
                os.environ["TCFP_REGISTRY"] = old


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
            "ENV TC_ID=zz-lint-test CC=/opt/tc/bin/x-gcc\n")

    def test_pinned_passes(self):
        self.assertEqual(self.lint_text(self.GOOD), [])

    def test_unpinned_refused(self):
        errs = self.lint_text(self.GOOD.replace("debian@sha256:" + "c" * 64, "debian:stable")
                              + "RUN apt-get install -y make\nCOPY local.sh /\n")
        text = "\n".join(errs)
        self.assertIn("not pinned", text)
        self.assertIn("apt-get", text)
        self.assertIn("build context", text)

    def test_scaffold_template_lints_clean(self):
        text = gen.dockerfile_text("zz-lint-test", "c", "https://x/tc.tar.xz", "d" * 64, "x-gcc")
        self.assertEqual(self.lint_text(text), [])

    def test_toolchain_is_the_tc_tar_download(self):
        extra = "ADD --checksum=sha256:" + "e" * 64 + " https://x/make.deb /debs/\n"
        self.assertEqual(self.lint_text(self.GOOD + extra), [])
        errs = self.lint_text(self.GOOD.replace(" /tc.tar", " /other.tar"))
        self.assertTrue(any("exactly one checksummed toolchain" in e for e in errs))

    def test_unchecksummed_download_refused(self):
        errs = self.lint_text(self.GOOD.replace("--checksum=sha256:" + "b" * 64 + " ", ""))
        self.assertTrue(any("without --checksum" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
