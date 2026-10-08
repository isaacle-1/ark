# ARK — Content sources & licensing

Policy: only content whose license permits redistribution / personal offline
copies is bundled or offered for download. Every catalog item records a license
and source URL (`catalog/*.json`, schema in `docs/`). URLs marked `UNVERIFIED`
have **not** been re-checked — treat them as leads, not facts.

## Phase 0 — nothing to download
The phase downloads nothing and references no remote resources.

## Verified sources (used by later phases)
These are candidates; each is re-verified at the phase that adds it:

| Source | Purpose | License | URL |
|---|---|---|---|
| Wikimedia / Kiwix ZIM dumps | library + offline wikipedia | CC BY-SA | https://download.kiwix.org/ |
| Gutenberg (offline HTML) | manuals / classics | public domain (US) | https://www.gutenberg.org/browse/categories |
| OpenStreetMap (OSM) extracts | maps + routing | ODbL | https://download.geofabrik.de/ (exported extracts) |
| Valhalla (valhalla/valhalla) | offline OSRM-grade routing | BSD-2 | https://github.com/valhalla/valhalla |
| Photon (komoot/photon) | offline geocoding | GPL-3 | https://github.com/komoot/photon |
| Argos Translate | offline translation models | MIT code / CC models | https://github.com/argosopentech/argos-translate |
| Airspy / rtl-sdr | SDR device support | GPL-2+ | vendor-provided tools |
| Project Gutenberg archive.org mirrors | backup copies | public domain | https://archive.org/details/gutenberg |

## Unverified leads
- EHRI / USHMM archival PDFs for the docs phase — **UNVERIFIED**, license status
  to confirm before wiring into a catalog.

## What never goes in
- Anything with a proprietary/web-only license, DRM, or "no redistribution"
  clause.
- Telemetry, ads, hijacked-download mirrors, or any URL we haven't recorded.

Any authority we are unsure about → leave out of the catalog, note it here, and
ask the user before adding.