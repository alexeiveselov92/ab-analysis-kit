# Changelog

All notable changes to ab-analysis-kit will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Once implementation begins, `CHANGELOG.md` is **authoritative for behavior changes**
— in particular every statistical deviation from the captured legacy baseline is
recorded here alongside an `ALGORITHM_VERSION` bump and a
[`statistics-changes.md`](docs/specs/statistics-changes.md) entry (never a silent
number change).

## [Unreleased]

### Added
- **Project initiation contract.** Architecture synthesized from the legacy
  `ab_testing` engine (statistical baseline) and detectkit (architecture / DX),
  validated by a 5-lens adversarial subagent quorum (all approve-with-changes).
  See the master plan [`docs/ru/project-initiation-spec.md`](docs/ru/project-initiation-spec.md)
  and the [specs index](docs/specs/00-overview.md): architecture, statistics
  baseline + changes + legacy method catalogue, cumulative-intervals/compute
  strategy, declarative config, data contract & reporting, A/A false-positive
  matrix, CLI & DX, branding & site, and the quorum must-fix gate.
- **Development scaffolding** (this session): packaging (`pyproject.toml`,
  `setup.py`, `MANIFEST.in`, `requirements.txt`), `pre-commit`, GitHub workflows
  (CI, publish-to-PyPI on tags, website), a minimal importable `abkit` package with
  a working `abk` CLI entry point (`abk --version`), and smoke tests.

### Locked decisions
- Greenfield storage (legacy dashboard is reference only); statistical math
  preserved as a baseline then improved deliberately.
- Fixed-horizon CI by default with honest cumulative-peeking FPR in `abk validate`;
  sequential (always-valid) CIs opt-in.
- ClickHouse-first; PostgreSQL/MySQL supported. Read-only exposures.

_Pre-development: no PyPI release yet. The first tagged release will populate a
versioned section here. Roadmap: [`ROADMAP.md`](ROADMAP.md)._
