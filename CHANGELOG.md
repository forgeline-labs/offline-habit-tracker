# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-16

### Added

- `app add FILE ID`, `app check FILE ID --date YYYY-MM-DD` and
  `app stats FILE --as-of YYYY-MM-DD`.
- Deterministic statistics: `checks`, `xp = 10 × checks`, `level = xp // 100`
  and a streak anchored to the `--as-of` date.
- Atomic JSON persistence (same-directory temporary file, flush, `fsync`,
  `os.replace`) with complete read/schema validation before any write.
- Exact CLI contract: `--version` / `--help`, JSON lines on stdout, one-line
  `error:` messages on stderr, exit codes `0`, `2`, `3`, `4`.
- Native Linux and Windows entrypoints (`./app`, `app.cmd`, `python app`).
- Public black-box test suite mapped to acceptance criteria AC-1 … AC-14.

