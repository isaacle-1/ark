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

## Vendored UI fonts (frontend redesign)
Shipped inside `ark/frontend/public/fonts/` as static files (no CDN, no fetch at
runtime). License texts travel next to each file (`*-OFL.txt`).

| Font | Purpose | License | URL |
|---|---|---|---|
| Barlow Condensed (Regular/Medium/SemiBold/Bold) | display headings / masthead | SIL OFL 1.1 | https://github.com/jpt/barlow |
| Roboto (variable wght+wdth) | body text | Apache 2.0 | https://github.com/google/fonts |
| Roboto Mono (variable wght) | data / identifiers | Apache 2.0 | https://github.com/google/fonts |

(Google Fonts source downloads: `raw.githubusercontent.com/google/fonts/main`,
checked out per-file, not via the Google Fonts API so nothing phones home.)

## Phase 1 — Kiwix ZIM library (verified, 19 entries)
`catalog/kiwix.json` is the source of truth. Every entry records `size`,
`sha256`, a `zim_url` (working mirror) plus `mirror_urls` (all verified
mirrors for that file), `license` and the OPDS source. All 19 entries were
re-verified against live sources (OPDS entry → `.meta4` size/checksum → each
mirror) by `scripts/verify_catalog.py` (`make verify-catalog`).

| ZIM (name / flavour) | Tier | License | Size |
|---|---|---|---|
| wikipedia_en_all / mini | 1 | CC BY-SA 4.0 | 14.4 GB |
| wikipedia_en_all / nopic | 2 | CC BY-SA 4.0 | 52.7 GB |
| wikipedia_en_all / maxi | 3 | CC BY-SA 4.0 | 127.4 GB |
| Wikipedia topic ZIMs (medicine, physics, chemistry, mathematics, history, geography, climate-change, knots, top) | — | CC BY-SA 4.0 | per-file |
| wikipedia_en_simple_all / mini | 1 | CC BY-SA 4.0 | per-file |
| wikibooks_en_all / nopic | 2 | CC BY-SA 4.0 | per-file |
| gutenberg_en_lcc-{pf,pd,ph} | — | public domain | per-file |
| devdocs_en_bash | — | mixed-per-source (per-doc; see OPDS) | 572 KB |
| ifixit_en_all | — | **UNVERIFIED** — see note | per-file |

**iFixit license — UNVERIFIED.** Added provisionally because the ZIM itself is
CC-BY-SA-3.0 friendly, but iFixit's contributor terms historically restrict
redistribution. It is left `unverified` in the catalog so the app refuses to
install it until the user confirms it is acceptable. **Ask the user before
enabling this entry.**

### Mirror reliability notes
- `wi.mirror.driftle.ss` blackholes requests from this box (curl returns `000`,
  connection stalls) — that's what made an early label "meta4 timeout" wrong.
- `verify_catalog.py` walks mirrors with a short timeout, **remembers dead hosts
  per run**, and stores the first responsive URL as `zim_url` with the rest as
  `mirror_urls`. Fast mirrors on this install: download.kiwix.org, lb.download
  .kiwix.org, ftp.nluug.nl.
- The download engine (in-app) tries mirrors in order and resumes to the last
  good offset when a mirror fails; a checksum mismatch deletes the partial.

## What never goes in
- Anything with a proprietary/web-only license, DRM, or "no redistribution"
  clause.
- Telemetry, ads, hijacked-download mirrors, or any URL we haven't recorded.

Any authority we are unsure about → leave out of the catalog, note it here, and
ask the user before adding.