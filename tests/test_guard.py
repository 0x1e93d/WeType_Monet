import tempfile
import unittest
from pathlib import Path

from scripts import guard


class GuardSelectionTests(unittest.TestCase):
    def test_selects_proc_scanner_with_kill(self):
        calls = {
            (0x1000, 0x1200): {"kill", "opendir", "readdir", "abort"},
            (0x2000, 0x2100): {"kill"},
            (0x3000, 0x3100): {"opendir", "readdir"},
        }

        chosen = guard.select_guard_functions(calls)

        self.assertEqual([span for span, _ in chosen], [(0x1000, 0x1200)])

    def test_prefers_function_with_crash_symbols(self):
        calls = {
            (0x1000, 0x1100): {"kill", "opendir"},
            (0x2000, 0x2200): {"kill", "opendir", "raise"},
        }

        chosen = guard.select_guard_functions(calls)

        self.assertEqual([span for span, _ in chosen], [(0x2000, 0x2200)])

    def test_no_kill_call_means_nothing_to_patch(self):
        calls = {(0x1000, 0x1100): {"opendir", "readdir", "closedir"}}

        self.assertEqual(guard.select_guard_functions(calls), [])

    def test_kill_without_proc_scan_is_rejected(self):
        with self.assertRaises(RuntimeError):
            guard.select_guard_functions({(0x1000, 0x1100): {"kill"}})


class GuardPatchTests(unittest.TestCase):
    def test_apply_ret_patch_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            lib = Path(directory) / "libwxhld.so"
            lib.write_bytes(b"\x00" * 16)

            self.assertTrue(guard.apply_ret_patch(lib, 4))
            self.assertEqual(lib.read_bytes()[4:8], guard.RET_ARM64)
            self.assertFalse(guard.apply_ret_patch(lib, 4))

    def test_apply_ret_patch_rejects_out_of_range(self):
        with tempfile.TemporaryDirectory() as directory:
            lib = Path(directory) / "libwxhld.so"
            lib.write_bytes(b"\x00" * 4)

            with self.assertRaises(ValueError):
                guard.apply_ret_patch(lib, 4)

    def test_prologue_detection(self):
        self.assertTrue(guard._looks_like_prologue(0xA9BF7BFD))  # stp x29, x30, [sp, #-16]!
        self.assertTrue(guard._looks_like_prologue(0xD104C3FF))  # sub sp, sp, #0x130
        self.assertFalse(guard._looks_like_prologue(0xD503201F))  # nop
        self.assertFalse(guard._looks_like_prologue(0xD1000420))  # sub x0, x1, #1


class GuardDecodeTests(unittest.TestCase):
    def test_decode_adrp_from_plt_stub(self):
        # 4.0.2 libwxhld.so 的 PLT 桩: adrp x16, #0x23af000 @ 0x22bcd24
        self.assertEqual(guard._decode_adrp(0xF0000790, 0x22BCD24), 0x23AF000)

    def test_decode_ldr_uimm_from_plt_stub(self):
        # 同上: ldr x17, [x16, #0xc88]
        self.assertEqual(guard._decode_ldr_uimm(0xF9464611), 0xC88)

    def test_decode_rejects_other_instructions(self):
        self.assertIsNone(guard._decode_adrp(0xD503201F, 0x1000))
        self.assertIsNone(guard._decode_ldr_uimm(0xD503201F))


if __name__ == "__main__":
    unittest.main()
