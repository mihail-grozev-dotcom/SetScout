# SetScout

**A local Steam Achievement Sets discovery and limited-time achievement tracker.**

SetScout helps you browse games reported to use Steam's newer Achievement Sets, verify candidates against public Steam achievement pages, and watch for archived or potentially time-limited achievements. It is a small, zero-dependency Python + JavaScript project designed to run on a Windows PC.

![SetScout interface](docs/preview.jpg)

> [!IMPORTANT]
> **The catalogue is not exhaustive and the scanner is experimental.** Games in the initial catalogue are *reported* or *seeded*, not automatically Steam-verified. Limited-time deadlines entered by users are reports, not automatic Steam confirmations. An empty results list does not mean that no affected games exist.

## Features

- Browse and search the initial Achievement Sets catalogue.
- Discover new candidates from Steam's public most-played and featured feeds; verify candidate achievement pages when Steam exposes set data.
- Distinguish **Steam-verified**, **reported**, and **unconfirmed** games, with last-checked timestamps.
- Filter by DLC sets, mode sets, and confirmed entries.
- Record **limited-time**, **archived**, or **unknown** achievement/set reports, including source links and optional deadlines.
- Watch saved games, see an activity feed and upcoming deadlines, and optionally receive browser desktop notifications while the page is open.
- Periodically scan a limited number of candidate and existing games while the local server runs; or scan manually.
- **Check all unverified games** in a single resumable-in-browser, server-side run with live progress, per-result counts, and a stop button. Each existing unverified catalogue entry is checked at most once per run; candidates are scanned separately.

## Quick start (Windows)

1. Install **Python 3.10+** (no `pip install` needed).
2. Download the repository as a ZIP using **Code → Download ZIP**, then extract it.
3. Double-click **`Start_SetScout.bat`**. Alternatively, open a terminal in the folder and run `py server.py` or `python server.py`.
4. Open **http://127.0.0.1:8765** in your browser.
5. Use **Find & verify new games** to test live discovery, or open a game to add a source-linked report.

The terminal must remain open while you use the app. Stop it with `Ctrl+C`.

### Mac/Linux

```bash
python3 server.py
```

Then visit http://127.0.0.1:8765.

## Check all unverified games

In the **Automatic game discovery** panel, select **Check all unverified** to check every listed game that is not yet Steam-verified. The scan continues in the local Python server if you close the browser tab, but ends if you close the server. Progress and results are visible in the scanner panel; you can stop after the current Steam request. The scan rate is deliberately limited to avoid overloading Steam.

This is **not** a scan of every game on Steam, nor does it automatically check the separate candidate queue. If Steam cannot confirm a game (for example, due to a rate limit), the entry stays unverified and its last-check status is recorded. Run the button again if you want to retry those entries.

## Starter data correction (Crusader Kings III)

Earlier releases accidentally associated **Crusader Kings III** with Steam App ID **1151640**, which belongs to **Horizon Zero Dawn Complete Edition**. Its correct App ID is **1158310**.

When the server starts after this update, it repairs **only** the wrongly named legacy entry in your local `games.json`, preserving source-linked user reports. It clears the previous Steam verification, set details and timestamps for that one entry, because they belonged to the wrong title. It also excludes invalid automated events, migrates related report/deadline events and preserves saved-game stars where there is no separate legitimate Horizon entry. The original database is backed up to `games.json.before-appid-fix.bak` before the correction. Restart the server and recheck the corrected game to verify it.

The checker now compares a recognizable Steam achievements-page title to the expected game name before marking a scan as verified. Name mismatches remain unverified.

## Your data stays local

On first startup, the server creates **`games.json`** from the included **`games.example.json`**. This local file stores your discovered games, verification history, reports, and activity records. Browser `localStorage` holds your starred watchlist and notification state.

**`games.json` is ignored by Git** so personal scan/report data should never be committed accidentally. Back it up when upgrading to a new version. No Steam login, API token, or account access is required.

If you've used SetScout v2/v3, copy your existing `games.json` into this folder **before** starting the new server to retain your data. Do not overwrite that file with `games.example.json`.

The server only listens on `127.0.0.1` (localhost). **Do not deploy the current Python server directly to the public internet:** its write endpoints were designed for local trusted use, not for a public multi-user service. Hosting the HTML alone (for example on GitHub Pages) does **not** provide the discovery/reporting API.

## What statuses mean

| Status | Interpretation |
| --- | --- |
| Steam verified | The scanner detected eligible set information on a public Steam achievement page on a previous successful check. |
| Reported | Included via a launch/developer/community report or user-supplied source; not necessarily independently verified by the scanner. |
| Active limited-time (reported) | A user recorded a potential time-limited achievement or set; its deadline/source requires manual verification. |
| Archived | Explicit archival evidence was observed in accessible public Steam data, or a source-linked user report claims it was archived; the UI distinguishes the two. |
| Unknown | Not enough information to determine temporal availability. |

A **passed deadline is not automatically proof of archival**. Steam may hide archived items from users who never unlocked them, and a missing flag is not proof of absence. Steam's public HTML is not a supported permanent API and may change or rate-limit requests.

## Project layout

```text
SetScout/
├── app.js                   # Front-end logic
├── index.html               # UI and styles
├── server.py                # Local HTTP server and Steam checker
├── games.example.json       # Public starter catalogue
├── Start_SetScout.bat       # Windows launcher
├── docs/preview.jpg         # Screenshot
├── tests/                   # Python unit tests
└── .github/workflows/       # GitHub Actions test workflow
```

## Run the tests

```bash
python -m unittest discover -s tests -v
```

Tests use mocked Steam responses where appropriate; they do **not** certify that live Steam requests will work under all conditions.

## Known limitations & roadmap

- Keyless discovery uses selected popular/featured feeds; optional keyed store indexing is much broader, but games can still be missed due to public API coverage or failed checks.
- Public achievement HTML is subject to change, geolocation, visibility, and rate limits.
- Source-linked limited-time reports and deadlines are entered manually; there is no reliable public expiry-date feed.
- Browser notifications require permission and an open page; this is not a background push service.
- Candidate scanning is intentionally conservative to avoid unnecessary load on Steam.

Possible future improvements: more complete achievement-set detail, systematic rechecking of older negative results, validated developer-announcement sources, and authenticated multi-device sync.

## Attribution

SetScout is an **unofficial, independent** community utility. It is not affiliated with, endorsed by, or supported by Valve or Steam. Steam and associated game names/imagery belong to their respective owners.

No open-source license has been chosen for this initial release. The repository owner can add one after choosing how others may reuse or contribute to the code.

## Full Steam store discovery (v5/v6)

SetScout now has an optional, much broader discovery index based on Steam's
official **IStoreService/GetAppList** Web API. The older keyless
`ISteamApps/GetAppList/v2` endpoint is deprecated and should not be used.

1. Get your own Steam Web API key at <https://steamcommunity.com/dev/apikey>
   (Steam may impose eligibility requirements).
2. Create a file named **`.steam_api_key`** beside `server.py` and paste only
   your API key into it, without quotes. Alternatively, configure the
   `STEAM_WEB_API_KEY` environment variable. **Never commit or share your key.**
3. Restart SetScout and click **Index / resume store catalogue**. Steam results
   are paginated and stored in `steam_catalog.sqlite3`, not added to your
   smaller candidate queue or `games.json`. The initial full index can be
   resumed if interrupted. No app in this index is assumed to support custom
   Achievement Sets before a successful check.
4. Click **Check next 50 indexed games** to verify the next batch. This is
   deliberately rate-limited and stores both positive and negative observations.
   Confirmed custom-set games are promoted to your normal SetScout catalogue.
   Stop the server to stop scanning. If Steam returns HTTP 429, scanning pauses.

This is **not instantaneous verification of the entire Steam catalogue**:
indexing is fast, but checking individual achievement pages requires separate
network requests and may take a long time. Steam can rate-limit requests, hide
content, or change public markup. Full-store indexing requires the key; the
existing featured/most-played discovery remains available without it.

The files `.steam_api_key` and `steam_catalog.sqlite3*` are excluded from Git.
Your old `games.json` and personal reports are unchanged by indexing.

### Continuous store discovery (v6)

In **Full Steam store discovery**, click **Continuous discovery: OFF** to opt in.
While the local Python server remains running, SetScout will:

- Resume incomplete full-store indexing automatically using the saved cursor.
- Check pending indexed games sequentially in batches of 50, with a 1.3-second
  delay between page requests and a 30-second gap between batches. These limits
  are conservative defaults, not a guarantee Steam won't rate-limit you.
- Refresh the completed store catalogue about once a day using Steam's
  `if_modified_since` API parameter. The refresh uses a saved cursor and a
  five-minute overlap so interrupted refreshes can resume without losing
  changes during a run.
- Save every completed game observation in `steam_catalog.sqlite3` and promote
  positive Achievement Set matches into the existing `games.json` catalogue.

The preference is **off by default**, stored locally and remembered across
server restarts. Click **Continuous discovery: ON** to pause at the next safe
check boundary. The **Stop** button pauses active work and disables continuous
mode; a normal server shutdown preserves the preference for the next start.

If Steam returns **HTTP 429**, continuous scanning pauses and the rate-limited
game stays **pending** for a later retry. The app does not automatically retry
rate-limited pages; wait for Steam to permit more requests before switching
continuous mode on again. HTTP errors other than 429 are recorded as errors;
unknown/missing evidence is never treated as confirmation that sets are absent.

After the first full index completes, **Refresh Steam catalogue** triggers an
incremental refresh manually. In continuous mode, the daily refresh happens
without pressing the button. Refreshed games retain previous scan observations;
metadata updates alone do not automatically recheck an older negative result.

**Expect the first verification sweep to take days**, not minutes, for a
100,000+-game catalogue. The server must stay running. Users can disable the
feature to avoid background traffic. No background work occurs while SetScout
is closed.
