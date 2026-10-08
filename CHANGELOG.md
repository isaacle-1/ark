# Changelog

All notable changes to ARK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Phase 0: foundation — config system, structured JSON logging with request IDs,
  health endpoints, auth (local accounts, argon2), background job queue skeleton,
  sidecar supervisor framework, `ark` CLI (`init`, `serve`, `doctor`,
  `support-bundle`), in-app log viewer with SSE live tail, dashboard shell,
  React + Vite + Tailwind frontend (PWA), install script + systemd unit,
  GitHub Actions CI and release workflows.
- Phase 1: information library — 19 verified Kiwix ZIM catalog entries with
  tiers and per-entry license/source (`catalog/kiwix.json`) plus
  `scripts/verify_catalog.py` mirror verifier; resumable, mirror-failing,
  hash-verified ZIM download engine (`ark/library.py`); `kiwix-serve` sidecar
  managed by the supervisor and proxied at `/svc/kiwix/`; manual uploads
  (streamed, sanitized filenames, inline serving); `/api/library/…` API with
  admin install/delete; Library page in the SPA (catalog table, progress bars,
  install/cancel/delete, kiwix reader iframe); `ark doctor` library check;
  62 KB test fixture ZIM + engine/router/integration e2e coverage; install.sh
  fetches pinned kiwix-tools.
