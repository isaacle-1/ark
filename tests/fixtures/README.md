# Test fixtures

- `ark-fixture.zim` — tiny (~60 KB) ZIM archive used by unit/integration/e2e
  tests. Original short articles (written for ARK, no third-party content).
- `fixture-src/` — the HTML/PNG sources; regenerate with `make fixture`
  (requires `apt install zim-tools`).

Do not replace this with a real content pack: tests must stay offline,
deterministic and < 1 MB.
