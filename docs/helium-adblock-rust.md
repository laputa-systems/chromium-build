# Native adblock (adblock-rust) for Helium: audit

The goal is content blocking in Helium without uBlock Origin's per-profile
filter compile, which cost bctl's Nanos sessions about 4 s of `up`, 850 MiB of
VMM memory and most of their idle CPU (`~/d/bctl/docs/performance.md`, Results
on Nanos). bctl now starts Helium with uBlock disabled and blocks no ads.

Helium has an experimental native integration of Brave's `adblock` crate. This
note records what it contains and how far it is from our pin. Nothing here has
been compiled yet.

## The experiment

- Branch `adblock-rust` of `imputnet/helium`, one commit `88f7dda1`
  (2026-10-10), titled "helium/core: adblock-rust (EXPERIMENT, DO NOT USE OR
  MERGE)". Its base is `aec023c7` (Chromium 155.0.8059.39); on 2026-11-10 it
  was 84 commits behind Helium `main` (Chromium 157.0.8103.50).
- Our pin, `helium_core` `2f81b9ca` in `config/helium.inputs.lock` (Chromium
  156.0.8078.79), is 69 commits ahead of that base and 15 behind `main`.
- It adds 15 patches under `patches/helium/core/adblock/` (about 9,000 lines),
  `devutils/setup_adblock.py`, and a `helium_filters` entry in `deps.ini`
  (bundled lists from `imputnet/helium-filters` release `202608241612`, pinned
  by SHA-256, unpacked to `third_party/helium_filters`). The other 21 files it
  touches are context refreshes of existing Helium patches.
- It is opt-in: `ENABLE_ADBLOCK=1` makes `setup_adblock.py` set the GN arg
  `enable_native_adblock = true`. uBlock Origin stays in `deps.ini` as a
  separate component.

## How it fits into Chromium

| Patch group | What it does |
| --- | --- |
| `adblock-rust-engine`, `adblock-buildflags` | A second Rust third-party workspace, `//third_party/rust/helium`, with its own `Cargo.toml`/`Cargo.lock` under `components/helium_adblock/rs/crates`, built by a patched `gnrt` (crate path and rustc metadata prefixes, build-script name prefix) and `build/rust/cargo_crate.gni` |
| `adblock-engine-bridge`, `adblock-core-api`, `adblock-engine-lifecycle`, `adblock-filter-sources`, `adblock-service*` | `components/helium_adblock`: the Rust bridge, engine lifecycle, filter sources and subscriptions, refresh |
| `adblock-bundled-filters` | Ships the `helium_filters` lists as resources |
| `adblock-chrome-runtime`, `adblock-chrome-network` | Per-profile service, and request blocking through a `URLLoaderFactory` proxy installed from `ChromeContentBrowserClient` (browser process, so headless sessions are covered) |
| `adblock-blink-cosmetic-filtering` | Cosmetic (element hiding) filtering in the renderer |
| `adblock-desktop-settings` | Settings UI on the privacy page |
| `adblock-review-fixes` | Follow-up fixes across the above |

C++ reaches the engine through a `cxx` bridge (`adblock-engine-bridge`). A
WebSocket wrapper sits beside the URL loader proxy (`adblock-review-fixes`,
`helium_adblock_websocket.cc`). WebTransport is not intercepted, there is no
CNAME uncloaking (Helium's uBlock build has it), and the commit adds no tests.
Blocking can be switched off per profile with the pref
`helium.adblock.enabled`.

Every request whose match is not cached waits for an asynchronous call to the
engine's worker sequence before its loader starts
(`HeliumAdBlockService::MatchRequest` posts `AdBlockEngineWorker::MatchRequest`
and fills `match_cache_`; cosmetic resources use a second cache). The engine's
own matching is fast; this hop is what to measure, end to end, against
uBlock's in-process `webRequest` path.

The engine is `adblock` 0.13.2 with features `css-validation` and
`full-regex-handling`. Its lockfile resolves 77 packages: 36 come from crates
Chromium already vendors through `[patch.crates-io]` path overrides, and 41
come from crates.io (`adblock`, `regex`, `selectors`, `cssparser`,
`flatbuffers`, `serde`, `url`, `phf*` and others). `gnrt_config.toml` already
declares the build-script outputs of `serde`, `serde_core` and `selectors`,
and allows `unsafe` only where those crates need it.

## Against our pin

Applied in order to the 33 files they touch from the prepared Chromium 156
tree in the `helium-0.20.3.1-headless-work-amd64` volume (read-only copy, no
build):

- 13 of the 15 patches apply cleanly, some with offsets or fuzz.
- `adblock-blink-cosmetic-filtering`: 1 of 5 hunks fails in
  `chrome/renderer/chrome_content_renderer_client.cc`, a removed trailing
  blank line whose context moved.
- `adblock-desktop-settings`: one hunk each fails in
  `chrome/browser/resources/settings/BUILD.gn` and
  `privacy_page/privacy_page.ts`, list insertions whose neighbouring lines
  changed between 155 and 156.

All three are mechanical. Whether the new code compiles against Chromium 156's
interfaces (the URL loader proxy, profile keyed services, the renderer
client) is not known until it is built.

## What our build needs that Helium's does not

- **Network only in `fetch`.** `setup_adblock.py` runs `gnrt vendor`, which
  downloads crates. Here `fetch` must download the 41 crates (and
  `helium-filters`) and pin them by the lockfile's checksums in
  `config/helium.inputs.lock`, and `prepare` must vendor from those files
  offline.
- **`prepare-helium.py`** unpacks only `search_engines_data` and
  `ublock_origin` from `deps.ini`; it must add `helium_filters`, apply the
  adblock series, and run the workspace setup with `ENABLE_ADBLOCK=1`.
- **musl and the pinned Rust 1.100 toolchain.** The crates are pure Rust
  apart from build scripts; nothing in the list links C, but the build is
  unproven on our musl target.
- **Licences.** `adblock` is MPL-2.0; the patches are GPL-3.0 (Helium). Both
  must enter the package's licence notices.

## Builds to compare

uBlock and the native engine must not both be active, or neither measurement
means anything. The experiment keeps uBlock in `deps.ini`, so the native build
must also drop the `ublock_origin` component and the patches that load it.
Three configurations then need only two packages:

| Configuration | Package |
| --- | --- |
| uBlock | the current package |
| native | a build with the adblock series and no uBlock |
| no blocker | the native build with `helium.adblock.enabled` false |

A build-time `ublock | native | none` selector would cost a full ThinLTO
build per value and is not needed for the comparison. Measure, on the same
Chromium revision, list snapshot, pages and host: cold start, first-load
blocking, browser and renderer RSS, idle CPU, page-load p50 and p95, and the
per-request delay of the proxy.

Gaps to close before the native build replaces uBlock: tests (first-party
context through nested frames and workers, redirects, scriptlets, dynamic
element hiding), WebTransport, and CNAME uncloaking.
`docs/helium-acceptance.md`'s `bundled-ublock` check needs a native
counterpart that also proves uBlock is absent, with the pref-off build as the
negative control.

## Forward-port in this repository

`config/patches/helium/adblock-*.patch` carry the experiment refreshed for
Chromium 156, applied as local patches after Helium's own series
(`config/helium.patch-disposition.json`). The three failing hunks were
redone by hand, so every patch applies with no offset or fuzz. Two departures
from the experiment:

- `adblock-desktop-settings` is left out; it only applied with fuzz 2 in six
  places, and nothing else depends on it. Blocking is controlled by the
  `helium.adblock.enabled` pref until the settings page is ported.
- `smallvec` in the workspace lockfile is 1.16.1, the version Chromium 156
  vendors (the experiment locked 155's 1.16.0).

The 41 crates and the `helium-filters` archive are `adblock_inputs` in
`config/helium.inputs.lock`, downloaded only by `fetch` and checked by size
and SHA-512. In `prepare`, `scripts/setup-helium-adblock.py vendor` checks
each crate against the workspace `Cargo.lock`, extracts it into
`third_party/rust/helium/chromium_crates_io/vendor/<name>-<epoch>`, and
unpacks the filter lists into `third_party/helium_filters`, with no network
and no cargo.

The GN files gnrt generates for that workspace are committed as
`adblock-rust-gn.patch`, as Chromium commits its own generated crate files,
because the builder cannot run gnrt: Chromium's x86-64 Rust toolchain ships a
glibc `cargo` that needs `libcurl-gnutls`, and the pruned tree lacks gnrt's
own dependencies. When the crate set changes, regenerate the patch with
`setup-helium-adblock.py generate` on a prepared tree, given a working musl
`cargo` (mounted over `third_party/rust-toolchain/bin/cargo`) and a gnrt
binary built from that tree's `tools/crates/gnrt`.

`enable_native_adblock` stays false in every profile, so the blocker is
compiled out. A few hunks are not behind the flag (a new
`ISOLATED_WORLD_ID_HELIUM_ADBLOCK` value, a reserved grit range in
`resource_ids.spec`, gnrt template variables), so the next package differs
from the last one and goes through the usual qualification.

Checked on 2026-11-10: `fetch` and `prepare` on a fresh work volume (50
inputs, 4 minutes, offline), Gate C complete, and the prepared adblock tree
identical to the one gnrt generated. With `enable_native_adblock = true` on a
copy of the last release build, the Rust engine and all 41 crates, the
`components/helium_adblock` libraries, `//chrome/browser/helium_adblock`, and
the patched browser and renderer objects compile with the pinned musl
toolchain; Ninja needed 494 steps before the `chrome` link.

## First native build (2026-11-10)

A copy of the last release build volume with the series applied and
`enable_native_adblock = true` built in 494 Ninja steps and 14 minutes,
ThinLTO link included (ccache 0 hits of 500, all new or header-affected
files). Packaged as an `r7` candidate (the lock's `package_revision` raised
only in a throwaway image) it passed `test-fast`, all eleven acceptance
checks, and qualified. Two packaging bugs surfaced and are fixed: the asset
group was not a data dependency, so the catalog was never staged
(`adblock-bundled-filters.patch`), and `stage-runtime` reused a stale
`runtime_deps` after an incremental build.

In a bctl Nanos session with `helium.adblock.enabled` set and uBlock
disabled, requests to `ad.doubleclick.net` and `google-analytics.com` from
an http page fail with `ERR_BLOCKED_BY_CLIENT`, and an ordinary request
does not. Requests from `about:blank` and `data:` documents are not
filtered (uBlock filtered them). The first boot also exposed a hydrogen
page-cache deadlock (a write from a file mapping at a mid-page offset),
fixed in hydrogen `d103587c`.

Against the same bctl tree with no blocker (Nanos, Linux amd64, quiet
host, 7 runs each):

| | No blocker | Native | uBlock (2026-11-10, earlier tree) |
| --- | --- | --- | --- |
| Cold `up` | 938 ms | 1070 ms | 5193 ms |
| VMM RSS 30 s after ready | 515 MiB | 683 MiB | 1696 MiB |
| VMM idle CPU | 0.30 % | 0.32 % | 7.6 % |
| Speedometer 3.1 | 28.2 | 22.6 | 24.1 |
| News site LCP / load event | 152 / 82 ms | 180 / 98 ms | 232 / 148 ms |

Native blocking beats uBlock everywhere except Speedometer, and costs 20
percent of Speedometer and about 18 percent of page load against no
blocker. The next section finds most of the Speedometer cost.

## Generic hiding as one native stylesheet

The renderer agent created an isolated world in every frame to run
`cosmetic_filter.js`, which collected class and id names and asked the
browser which generic rules matched them. An isolated world in a frame
slows the page's own DOM bindings, and that was most of the Speedometer
cost. Variants built on the same tree (Nanos, Linux amd64):

| Variant | What runs in the renderer | Speedometer 3.1 |
| --- | --- | --- |
| base | no blocker | 25.2–26.1 |
| N | isolated world in every frame (the experiment as ported) | 21.5–22.9 |
| C2 | page hide rules as a native user sheet; isolated world only for procedural rules | 23.8–25.0 |
| C4 | C2 plus every generic class and id rule in one shared sheet | 23.8–24.9 |

C4 is `adblock-native-cosmetic-css.patch`. The engine builds the generic
sheet (30,045 selectors, about 1.2 MB of CSS, 28 ms on the default lists)
when it loads. Each renderer process requests it once by id, keeps one
copy, and inserts it into every document as a user stylesheet along with
the page's own hide rules. Blink indexes class and id selectors by hash, so
matching stays cheap. A page whose exceptions remove generic rules gets a
filtered sheet in its cosmetic resources instead. Generic elements are
hidden at first paint; N hid them only after its script's round trip.
adblock-rust has no API that lists its generic rules, so
`adblock-crates/adblock-0.13.2.patch` adds
`Engine::generic_class_id_selectors()`, and prepare applies crate patches
after vendoring.

The generic sheet costs no Speedometer score or memory beyond the spread:
C4 and C2 score alike, and VMM RSS 30 s after ready is 660–689 MiB for C4
and 667–678 MiB for C2. Base is 505–511 MiB. The engine costs the 160–180
MiB, and N measures 579–669 MiB. The C4 rounds shared the host with
another agent's Firecracker VMs, so the Speedometer figures need a quiet
rerun before they count.

Two costs remain. About 6 percent of Speedometer against base, which C2
shows too and is most likely the URL loader proxy's async engine hop per
request. And the engine's memory.

## A first compile

The work volume keeps the last official ThinLTO build in
`out/helium-wayland-release` (9.4 GiB, built through ccache). Applying the
series there and running Ninja would recompile only the changed and new
files, but `chrome` is re-linked with ThinLTO, which is the expensive step.
That is the cheapest proof that the forward-port compiles; it is not done.
