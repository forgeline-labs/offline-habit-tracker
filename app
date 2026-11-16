#!/usr/bin/env python3
"""offline-habit-tracker 0.1.0 — local habit tracker CLI.

Standard library only.  The public interface is documented in README.md.
"""

import datetime
import json
import os
import re
import sys
import tempfile

VERSION = "0.1.0"
PRODUCT = "offline-habit-tracker"

MAX_HABITS = 100
MAX_DATES = 10000
MAX_FILE_BYTES = 32 * 1024 * 1024  # 33554432

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_IO = 3
EXIT_MISSING_HABIT = 4

ID_RE = re.compile(r"[a-z][a-z0-9-]{0,31}\Z", re.ASCII)
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
BOM = b"\xef\xbb\xbf"

HELP = """usage: app <command> ...

offline-habit-tracker 0.1.0 — local habit tracker with date-based
check-ins, exact streaks and XP. Offline, standard library only.

commands:
  app add FILE ID                     create a habit (idempotent)
  app check FILE ID --date YYYY-MM-DD record a check-in for a date
  app stats FILE --as-of YYYY-MM-DD   show derived statistics

options:
  --help      show this help and exit
  --version   print the version and exit
  --date      check-in date (YYYY-MM-DD) for 'check'
  --as-of     reference date (YYYY-MM-DD) for 'stats'

arguments:
  FILE        path to the JSON state file
  ID          habit id, ASCII [a-z][a-z0-9-]{0,31}

exit codes: 0 success, 2 invalid input, 3 I/O or corrupt state,
4 habit not found.
"""


class AppError(Exception):
    """A contract error carrying the process exit code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _usage(message):
    return AppError(EXIT_USAGE, message)


def _io(message):
    return AppError(EXIT_IO, message)


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

def valid_id(value):
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def parse_date(value):
    """Return a datetime.date for a strict YYYY-MM-DD string, else AppError(2)."""
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise _usage("invalid date, expected YYYY-MM-DD")
    year = int(value[0:4])
    month = int(value[5:7])
    day = int(value[8:10])
    try:
        return datetime.date(year, month, day)
    except ValueError:
        raise _usage("invalid Gregorian date")


# ---------------------------------------------------------------------------
# State file reading / validation
# ---------------------------------------------------------------------------

def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite number")


def _state_error(message):
    return AppError(EXIT_IO, "invalid state file: " + message)


def _parse_state_text(data, path):
    if len(data) > MAX_FILE_BYTES:
        raise _state_error("file exceeds 32 MiB")
    if data.startswith(BOM):
        raise _state_error("byte order mark is not allowed")
    if not data:
        raise _state_error("empty file")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _state_error("not valid UTF-8")
    try:
        obj = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        raise _state_error("not valid JSON")
    _validate_state(obj)
    return obj


def _validate_state(obj):
    if not isinstance(obj, dict):
        raise _state_error("top level must be an object")
    if set(obj.keys()) != {"habits"}:
        raise _state_error("top level must contain exactly the 'habits' key")
    habits = obj["habits"]
    if not isinstance(habits, list):
        raise _state_error("'habits' must be an array")
    if len(habits) > MAX_HABITS:
        raise _state_error("more than 100 habits")
    previous_id = None
    for habit in habits:
        if not isinstance(habit, dict):
            raise _state_error("each habit must be an object")
        if set(habit.keys()) != {"id", "dates"}:
            raise _state_error("each habit must contain exactly 'id' and 'dates'")
        habit_id = habit["id"]
        dates = habit["dates"]
        if not valid_id(habit_id):
            raise _state_error("invalid habit id")
        if previous_id is not None and habit_id <= previous_id:
            raise _state_error("habits must be sorted by unique id")
        previous_id = habit_id
        if not isinstance(dates, list):
            raise _state_error("'dates' must be an array")
        if len(dates) > MAX_DATES:
            raise _state_error("more than 10000 dates for one habit")
        previous_date = None
        for date_text in dates:
            if not isinstance(date_text, str) or DATE_RE.fullmatch(date_text) is None:
                raise _state_error("dates must be YYYY-MM-DD strings")
            try:
                datetime.date(
                    int(date_text[0:4]),
                    int(date_text[5:7]),
                    int(date_text[8:10]),
                )
            except ValueError:
                raise _state_error("invalid Gregorian date in state")
            if previous_date is not None and date_text <= previous_date:
                raise _state_error("dates must be unique and sorted ascending")
            previous_date = date_text


def read_state(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise _io("cannot read state file: " + (exc.strerror or "I/O error"))
    return _parse_state_text(data, path)


# ---------------------------------------------------------------------------
# State file writing (atomic replacement)
# ---------------------------------------------------------------------------

def serialize_state(state):
    text = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def write_state(path, state):
    data = serialize_state(state)
    directory = os.path.dirname(os.path.abspath(path))
    fd = None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=directory, prefix=".offline-habit-tracker-", suffix=".tmp"
        )
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    except OSError as exc:
        raise _io("cannot write state file: " + (exc.strerror or "I/O error"))
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def find_habit(state, habit_id):
    for habit in state["habits"]:
        if habit["id"] == habit_id:
            return habit
    return None


def cmd_add(path, habit_id):
    if not valid_id(habit_id):
        raise _usage("invalid habit id")
    if os.path.exists(path):
        state = read_state(path)
    else:
        state = {"habits": []}
    habit = find_habit(state, habit_id)
    if habit is not None:
        return {"id": habit_id, "created": False}
    if len(state["habits"]) >= MAX_HABITS:
        raise _usage("cannot exceed 100 habits")
    state["habits"].append({"id": habit_id, "dates": []})
    state["habits"].sort(key=lambda item: item["id"])
    write_state(path, state)
    return {"id": habit_id, "created": True}


def cmd_check(path, habit_id, date_text):
    if not valid_id(habit_id):
        raise _usage("invalid habit id")
    parse_date(date_text)  # argument validation precedes state access
    state = read_state(path)
    habit = find_habit(state, habit_id)
    if habit is None:
        raise AppError(EXIT_MISSING_HABIT, "habit not found: " + habit_id)
    if date_text in habit["dates"]:
        return {"id": habit_id, "date": date_text, "added": False}
    if len(habit["dates"]) >= MAX_DATES:
        raise _usage("cannot exceed 10000 dates for one habit")
    habit["dates"].append(date_text)
    habit["dates"].sort()
    write_state(path, state)
    return {"id": habit_id, "date": date_text, "added": True}


def _streak_ending_on(considered_dates, as_of_text):
    if as_of_text not in considered_dates:
        return 0
    present = set(considered_dates)
    current = parse_date(as_of_text)
    streak = 0
    while current.isoformat() in present:
        streak += 1
        if current == datetime.date.min:
            break
        current -= datetime.timedelta(days=1)
    return streak


def cmd_stats(path, as_of_text):
    parse_date(as_of_text)
    state = read_state(path)
    rows = []
    for habit in state["habits"]:
        considered = [date for date in habit["dates"] if date <= as_of_text]
        checks = len(considered)
        xp = checks * 10
        rows.append(
            {
                "id": habit["id"],
                "checks": checks,
                "xp": xp,
                "level": xp // 100,
                "streak": _streak_ending_on(considered, as_of_text),
            }
        )
    return {"habits": rows}


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

def _split_options(tokens, name):
    """Split positional arguments from a single-valued --name option."""
    positionals = []
    value = None
    index = 0
    prefix = name + "="
    while index < len(tokens):
        token = tokens[index]
        if token == name:
            if value is not None or index + 1 >= len(tokens):
                raise _usage("missing or repeated " + name)
            value = tokens[index + 1]
            index += 2
        elif token.startswith(prefix):
            if value is not None:
                raise _usage("repeated " + name)
            value = token[len(prefix):]
            index += 1
        elif token.startswith("--"):
            raise _usage("unknown argument: " + token)
        else:
            positionals.append(token)
            index += 1
    return positionals, value


def parse_command(argv):
    command = argv[0]
    rest = argv[1:]
    if command == "add":
        positionals, extra = _split_options(rest, "--date")
        if extra is not None or len(positionals) != 2:
            raise _usage("usage: app add FILE ID")
        return cmd_add(positionals[0], positionals[1])
    if command == "check":
        positionals, date_text = _split_options(rest, "--date")
        if len(positionals) != 2 or date_text is None:
            raise _usage("usage: app check FILE ID --date YYYY-MM-DD")
        return cmd_check(positionals[0], positionals[1], date_text)
    if command == "stats":
        positionals, as_of_text = _split_options(rest, "--as-of")
        if len(positionals) != 1 or as_of_text is None:
            raise _usage("usage: app stats FILE --as-of YYYY-MM-DD")
        return cmd_stats(positionals[0], as_of_text)
    raise _usage("unknown command: " + command)


def configure_streams():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", newline="\n")
        except (AttributeError, ValueError):
            pass


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main(argv):
    configure_streams()
    if len(argv) == 1 and argv[0] in ("--help", "--version"):
        if argv[0] == "--version":
            sys.stdout.write(PRODUCT + " " + VERSION + "\n")
        else:
            sys.stdout.write(HELP)
        sys.stdout.flush()
        return EXIT_OK
    try:
        if not argv:
            raise _usage("missing command")
        result = parse_command(argv)
    except AppError as exc:
        message = " ".join(str(exc.message).split())
        sys.stderr.write("error: " + message + "\n")
        sys.stderr.flush()
        return exc.code
    except OSError as exc:
        message = " ".join(("I/O error: " + (exc.strerror or "")).split())
        sys.stderr.write("error: " + message + "\n")
        sys.stderr.flush()
        return EXIT_IO
    emit(result)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
