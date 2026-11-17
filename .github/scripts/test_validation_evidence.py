"""Portable checks of CI evidence gates; these do not simulate Windows execution."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "validation", Path(__file__).with_name("validate_windows_installer.py"))
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def report(self, extra="", names=None):
        names = names if names is not None else [
            "test_protect_unprotect_round_trips", "test_corrupt_blob_fails_closed",
            "test_second_launch_quits_without_hotkey_and_first_is_raised"]
        path = self.root / "tests.xml"
        path.write_text('<testsuites><testsuite>' + ''.join(
            f'<testcase name="{name}">{extra}</testcase>' for name in names)
            + '</testsuite></testsuites>', encoding="utf-8")
        return path

    def test_native_success_requires_named_cases(self):
        self.assertEqual(validation.check_native_results(self.report()),
                         {"status": "PASS", "passed": 3, "skipped": 0})

    def test_native_skips_failures_and_errors_are_rejected(self):
        for tag in ("skipped", "failure", "error"):
            with self.subTest(tag=tag), self.assertRaises(RuntimeError):
                validation.check_native_results(self.report(f"<{tag}/>"))

    def test_missing_native_coverage_is_rejected(self):
        for names in ([], ["some_other_test"]):
            with self.subTest(names=names), self.assertRaises(RuntimeError):
                validation.check_native_results(self.report(names=names))

    def test_manifest_detects_changed_missing_and_added_files(self):
        binary = self.root / "backend.exe"
        binary.write_bytes(b"original")
        before = validation.tree_manifest(self.root)
        binary.write_bytes(b"modified")
        self.assertNotEqual(before, validation.tree_manifest(self.root))
        binary.unlink()
        self.assertNotEqual(before, validation.tree_manifest(self.root))
        binary.write_bytes(b"original")
        (self.root / "unexpected.txt").write_bytes(b"")
        self.assertNotEqual(before, validation.tree_manifest(self.root))

    def test_fingerprint_known_hash_and_size(self):
        path = self.root / "artifact"
        path.write_bytes(b"abc")
        self.assertEqual(validation.fingerprint(path), {
            "bytes": 3,
            "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"})

    def test_pe_machine_gate_rejects_wrong_architecture_and_non_pe(self):
        path = self.root / "app.exe"
        data = bytearray(128)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 64)
        data[64:68] = b"PE\0\0"
        struct.pack_into("<H", data, 68, 0x8664)
        path.write_bytes(data)
        validation.check_pe(path, x64=True)
        struct.pack_into("<H", data, 68, 0x14C)
        path.write_bytes(data)
        with self.assertRaises(RuntimeError):
            validation.check_pe(path, x64=True)
        path.write_bytes(b"not an executable")
        with self.assertRaises(RuntimeError):
            validation.check_pe(path)


if __name__ == "__main__":
    unittest.main()
