"""Black-box tests for the offline habit tracker CLI.

Every test drives the public entrypoint as a subprocess; no private module
is imported.  Test names carry the acceptance criterion they exercise.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"


def run_app(*args, cwd=None):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, str(APP), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        env=env,
    )


def write_raw(path, data):
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode, **({} if mode == "wb" else {"encoding": "utf-8", "newline": "\n"})) as fh:
        fh.write(data)


def write_state(path, habits):
    payload = {"habits": habits}
    write_raw(path, json.dumps(payload, separators=(",", ":")) + "\n")


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.file = self.dir / "state.json"

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        return json.loads(result.stdout)

    def assert_error(self, result, code):
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertTrue(result.stderr.startswith("error:"))


class TestInterface(CliTestCase):
    def test_version_is_exact(self):
        result = run_app("--version")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "offline-habit-tracker 0.1.0\n")
        self.assertEqual(result.stderr, "")

    def test_help_mentions_contract(self):
        result = run_app("--help")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        for token in (
            "usage:",
            "app",
            "--help",
            "--version",
            "add",
            "check",
            "stats",
            "--date",
            "--as-of",
            "FILE",
            "ID",
        ):
            self.assertIn(token, result.stdout)

    def test_unknown_argument_exits_2(self):
        self.assert_error(run_app("add", self.file, "walk", "--bogus"), 2)

    def test_missing_required_argument_exits_2(self):
        self.assert_error(run_app("add", self.file), 2)
        self.assert_error(run_app("check", self.file, "walk"), 2)
        self.assert_error(run_app("stats", self.file), 2)
        self.assert_error(run_app(), 2)


class TestAdd(CliTestCase):
    def test_ac1_add_creates_then_is_noop(self):
        first = self.assert_ok(run_app("add", self.file, "walk"))
        self.assertEqual(first, {"id": "walk", "created": True})
        second = self.assert_ok(run_app("add", self.file, "walk"))
        self.assertEqual(second, {"id": "walk", "created": False})
        self.assertEqual(read_json(self.file), {"habits": [{"id": "walk", "dates": []}]})

    def test_add_rejects_invalid_id(self):
        for bad in ("Walk", "1walk", "wa_alk", "a" * 33, "wälk", ""):
            with self.subTest(bad=bad):
                self.assert_error(run_app("add", self.file, bad), 2)
        self.assertFalse(self.file.exists())

    def test_add_missing_parent_is_io_error(self):
        missing = self.dir / "nope" / "state.json"
        self.assert_error(run_app("add", missing, "walk"), 3)


class TestCheck(CliTestCase):
    def setUp(self):
        super().setUp()
        self.assert_ok(run_app("add", self.file, "walk"))

    def test_ac2_repeated_check_is_noop(self):
        first = self.assert_ok(run_app("check", self.file, "walk", "--date", "2026-01-01"))
        self.assertEqual(first, {"id": "walk", "date": "2026-01-01", "added": True})
        second = self.assert_ok(run_app("check", self.file, "walk", "--date", "2026-01-01"))
        self.assertEqual(second, {"id": "walk", "date": "2026-01-01", "added": False})
        stats = self.assert_ok(run_app("stats", self.file, "--as-of", "2026-01-01"))
        self.assertEqual(stats["habits"][0]["checks"], 1)

    def test_ac7_out_of_order_dates_are_sorted_on_disk(self):
        for day in ("03", "01", "02"):
            self.assert_ok(run_app("check", self.file, "walk", "--date", "2026-01-" + day))
        self.assertEqual(
            read_json(self.file)["habits"][0]["dates"],
            ["2026-01-01", "2026-01-02", "2026-01-03"],
        )

    def test_ac8_invalid_date_preserves_bytes(self):
        self.assert_ok(run_app("check", self.file, "walk", "--date", "2026-01-01"))
        before = self.file.read_bytes()
        for bad in (
            "2026-02-30",
            "2026-13-01",
            "0000-01-01",
            "10000-01-01",
            "2026-1-1",
            "2026/01/01",
            "２０２６-01-01",
            "true",
        ):
            with self.subTest(bad=bad):
                self.assert_error(run_app("check", self.file, "walk", "--date", bad), 2)
                self.assertEqual(self.file.read_bytes(), before)

    def test_ac9_missing_habit_exits_4(self):
        self.assert_error(run_app("check", self.file, "run", "--date", "2026-01-01"), 4)

    def test_validation_precedes_habit_lookup(self):
        self.assert_error(run_app("check", self.file, "run", "--date", "bad"), 2)
        self.assert_error(run_app("check", self.file, "bad id", "--date", "2026-01-01"), 2)

    def test_check_missing_file_exits_3(self):
        other = self.dir / "other.json"
        self.assert_error(run_app("check", other, "walk", "--date", "2026-01-01"), 3)


class TestStats(CliTestCase):
    def test_ac3_streak_and_xp(self):
        self.assert_ok(run_app("add", self.file, "walk"))
        for day in ("01", "02", "03"):
            self.assert_ok(run_app("check", self.file, "walk", "--date", "2026-01-" + day))
        stats = self.assert_ok(run_app("stats", self.file, "--as-of", "2026-01-03"))
        self.assertEqual(
            stats,
            {
                "habits": [
                    {"id": "walk", "checks": 3, "xp": 30, "level": 0, "streak": 3}
                ]
            },
        )

    def test_ac4_no_check_on_as_of_streak_zero(self):
        self.assert_ok(run_app("add", self.file, "walk"))
        self.assert_ok(run_app("check", self.file, "walk", "--date", "2026-01-01"))
        self.assert_ok(run_app("check", self.file, "walk", "--date", "2026-01-02"))
        stats = self.assert_ok(run_app("stats", self.file, "--as-of", "2026-01-03"))
        self.assertEqual(stats["habits"][0]["streak"], 0)
        self.assertEqual(stats["habits"][0]["checks"], 2)

    def test_ac5_future_date_is_ignored(self):
        self.assert_ok(run_app("add", self.file, "walk"))
        self.assert_ok(run_app("check", self.file, "walk", "--date", "2026-01-05"))
        stats = self.assert_ok(run_app("stats", self.file, "--as-of", "2026-01-04"))
        self.assertEqual(stats["habits"][0]["checks"], 0)
        self.assertEqual(stats["habits"][0]["streak"], 0)
        stats = self.assert_ok(run_app("stats", self.file, "--as-of", "2026-01-05"))
        self.assertEqual(stats["habits"][0]["checks"], 1)

    def test_ac6_ten_checks_give_xp_100_level_1(self):
        self.assert_ok(run_app("add", self.file, "walk"))
        for day in range(1, 11):
            self.assert_ok(
                run_app("check", self.file, "walk", "--date", "2026-01-%02d" % day)
            )
        stats = self.assert_ok(run_app("stats", self.file, "--as-of", "2026-01-10"))
        self.assertEqual(stats["habits"][0]["xp"], 100)
        self.assertEqual(stats["habits"][0]["level"], 1)
        self.assertEqual(stats["habits"][0]["streak"], 10)

    def test_ac12_year_one_does_not_underflow(self):
        self.assert_ok(run_app("add", self.file, "walk"))
        self.assert_ok(run_app("check", self.file, "walk", "--date", "0001-01-01"))
        stats = self.assert_ok(run_app("stats", self.file, "--as-of", "0001-01-01"))
        self.assertEqual(stats["habits"][0]["streak"], 1)
        self.assertEqual(stats["habits"][0]["checks"], 1)

    def test_ac9_stats_missing_file_exits_3(self):
        self.assert_error(run_app("stats", self.dir / "absent.json", "--as-of", "2026-01-01"), 3)

    def test_stats_does_not_mutate(self):
        self.assert_ok(run_app("add", self.file, "walk"))
        self.assert_ok(run_app("check", self.file, "walk", "--date", "2026-01-01"))
        before = self.file.read_bytes()
        self.assert_ok(run_app("stats", self.file, "--as-of", "2026-01-02"))
        self.assertEqual(self.file.read_bytes(), before)

    def test_habits_are_sorted_by_id(self):
        for habit_id in ("zebra", "alpha", "mike"):
            self.assert_ok(run_app("add", self.file, habit_id))
        stats = self.assert_ok(run_app("stats", self.file, "--as-of", "2026-01-01"))
        self.assertEqual([row["id"] for row in stats["habits"]], ["alpha", "mike", "zebra"])

    def test_empty_habit_reports_zeros(self):
        self.assert_ok(run_app("add", self.file, "walk"))
        stats = self.assert_ok(run_app("stats", self.file, "--as-of", "2026-01-01"))
        self.assertEqual(
            stats["habits"][0],
            {"id": "walk", "checks": 0, "xp": 0, "level": 0, "streak": 0},
        )


class TestCorruptState(CliTestCase):
    def _assert_corrupt(self, content):
        write_raw(self.file, content)
        before = self.file.read_bytes()
        self.assert_error(run_app("add", self.file, "walk"), 3)
        self.assertEqual(self.file.read_bytes(), before)
        self.assert_error(run_app("stats", self.file, "--as-of", "2026-01-01"), 3)
        self.assertEqual(self.file.read_bytes(), before)

    def test_ac10_invalid_json(self):
        self._assert_corrupt("{not json")

    def test_ac10_valid_json_wrong_shape(self):
        self._assert_corrupt('{"habits": {}}')
        self._assert_corrupt('[]')
        self._assert_corrupt('{"habits": [], "extra": 1}')

    def test_duplicate_keys_rejected(self):
        self._assert_corrupt('{"habits": [], "habits": []}')

    def test_non_object_habit_rejected(self):
        self._assert_corrupt('{"habits": ["walk"]}')

    def test_habit_extra_field_rejected(self):
        self._assert_corrupt('{"habits": [{"id": "walk", "dates": [], "x": 1}]}')

    def test_zero_byte_file_rejected(self):
        self._assert_corrupt(b"")

    def test_bom_rejected(self):
        self._assert_corrupt(b'\xef\xbb\xbf{"habits": []}\n')

    def test_invalid_utf8_rejected(self):
        self._assert_corrupt(b'{"habits": [], "x": "\xff"}')

    def test_boolean_date_rejected(self):
        self._assert_corrupt('{"habits": [{"id": "walk", "dates": [true]}]}')

    def test_non_ascii_id_rejected(self):
        self._assert_corrupt('{"habits": [{"id": "wälk", "dates": []}]}')

    def test_unsorted_or_duplicate_dates_rejected(self):
        self._assert_corrupt(
            '{"habits": [{"id": "walk", "dates": ["2026-01-02", "2026-01-01"]}]}'
        )
        self._assert_corrupt(
            '{"habits": [{"id": "walk", "dates": ["2026-01-01", "2026-01-01"]}]}'
        )

    def test_impossible_date_in_state_rejected(self):
        self._assert_corrupt('{"habits": [{"id": "walk", "dates": ["2026-02-30"]}]}')

    def test_unsorted_habits_rejected(self):
        self._assert_corrupt(
            '{"habits": [{"id": "b", "dates": []}, {"id": "a", "dates": []}]}'
        )

    def test_oversized_file_rejected(self):
        with open(self.file, "wb") as fh:
            fh.write(b" " * (32 * 1024 * 1024 + 1))
        before = self.file.read_bytes()
        self.assert_error(run_app("add", self.file, "walk"), 3)
        self.assertEqual(self.file.read_bytes(), before)


class TestLimits(CliTestCase):
    def test_ac14_101st_habit_exits_2_without_mutation(self):
        habits = [{"id": "h%03d" % i, "dates": []} for i in range(100)]
        write_state(self.file, habits)
        before = self.file.read_bytes()
        self.assert_error(run_app("add", self.file, "new"), 2)
        self.assertEqual(self.file.read_bytes(), before)

    def test_ac14_10001st_date_exits_2_without_mutation(self):
        import datetime

        start = datetime.date(2000, 1, 1)
        dates = [(start + datetime.timedelta(days=i)).isoformat() for i in range(10000)]
        write_state(self.file, [{"id": "walk", "dates": dates}])
        before = self.file.read_bytes()
        self.assert_error(run_app("check", self.file, "walk", "--date", "1600-01-01"), 2)
        self.assertEqual(self.file.read_bytes(), before)

    def test_date_already_at_limit_is_still_a_noop(self):
        import datetime

        start = datetime.date(2000, 1, 1)
        dates = [(start + datetime.timedelta(days=i)).isoformat() for i in range(10000)]
        write_state(self.file, [{"id": "walk", "dates": dates}])
        before = self.file.read_bytes()
        result = self.assert_ok(
            run_app("check", self.file, "walk", "--date", dates[5000])
        )
        self.assertFalse(result["added"])
        self.assertEqual(self.file.read_bytes(), before)


class TestUnicodePaths(CliTestCase):
    def test_ac13_unicode_and_spaces_survive_restart(self):
        folder = self.dir / "hébitos con espacios ü"
        folder.mkdir()
        state = folder / "mis hábitos.json"
        self.assert_ok(run_app("add", state, "walk"))
        self.assert_ok(run_app("check", state, "walk", "--date", "2026-01-01"))
        self.assert_ok(run_app("check", state, "walk", "--date", "2026-01-02"))
        stats = self.assert_ok(run_app("stats", state, "--as-of", "2026-01-02"))
        self.assertEqual(stats["habits"][0]["streak"], 2)
        self.assertEqual(
            read_json(state),
            {"habits": [{"id": "walk", "dates": ["2026-01-01", "2026-01-02"]}]},
        )


@unittest.skipIf(os.name == "nt", "POSIX directory permissions are required")
@unittest.skipIf(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    "root bypasses directory permission bits",
)
class TestWriteFailure(CliTestCase):
    def test_ac11_write_failure_keeps_original_bytes(self):
        write_state(self.file, [{"id": "walk", "dates": ["2026-01-01"]}])
        before = self.file.read_bytes()
        os.chmod(self.dir, stat.S_IRUSR | stat.S_IXUSR)
        try:
            result = run_app("check", self.file, "walk", "--date", "2026-01-02")
        finally:
            os.chmod(self.dir, stat.S_IRWXU)
        self.assert_error(result, 3)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(
            sorted(p.name for p in self.dir.iterdir()), ["state.json"],
            "temporary file must be removed",
        )

    def test_ac11_add_cannot_create_in_read_only_directory(self):
        os.chmod(self.dir, stat.S_IRUSR | stat.S_IXUSR)
        try:
            result = run_app("add", self.file, "walk")
        finally:
            os.chmod(self.dir, stat.S_IRWXU)
        self.assert_error(result, 3)
        self.assertFalse(self.file.exists())


if __name__ == "__main__":
    unittest.main()
