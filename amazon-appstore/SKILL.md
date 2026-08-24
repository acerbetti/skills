---
name: amazon-appstore
description: Use when pushing Amazon Appstore listing metadata (title, descriptions, feature bullets, keywords, release notes) or Fire TV / phone screenshots + artwork to the Amazon Developer Console for Clocks in Motion. Triggers on "push amazon metadata", "update Amazon Appstore listing", "upload Fire TV screenshots", "Amazon App Submission API".
---

# Amazon Appstore — Store Listing Push

## Overview

Pushes the Amazon **store listing** (title, short/long description, feature bullets,
keywords, release notes) and images (Fire TV + phone screenshots, icons, Fire TV
artwork) via the **Amazon App Submission API**.

- Script:  `~/.claude/skills/amazon-appstore/push-amazon.py`
- Wrapper: `~/.claude/skills/amazon-appstore/push-amazon.sh`

**No venv / deps** — the Amazon API is plain REST + a bearer token, so this runs on
system `python3` (unlike the sister `google-play` and `app-store-connect` skills).

**Scope boundary:** store listing only. The APK/AAB is owned by CI (the Fire TV
publish job). This script never uploads a binary or touches targeting/availability.

---

## ⚠️ How Amazon differs from Google Play (read this first)

Same edit-transaction *shape* as Play, but two big behavioural differences:

| | Google Play | Amazon Appstore |
|---|---|---|
| Auth | service-account JSON → OAuth2 | **LWA client_credentials** → bearer token (1 h) |
| Commit semantics | commit *stages* (Managed Publishing gate) | **commit = submit for review + publish immediately — no gate** |
| Dry-run | stages a real edit, validates, discards | **local preview only** (Amazon has no server-side validate) |
| Partial update | PATCH | **no PATCH** — PUT replaces the whole listing (script GETs + merges) |
| Concurrency | edits are independent | **one active edit at a time**; PUT/DELETE/commit need an **`If-Match` ETag** |
| Extra text fields | title/short/full only | also **`featureBullets`** + **`keywords`** (Amazon-only) |
| Release notes | track release → owned by CI | **`recentChanges`** lives on the listing → pushed here |

Because `--commit` **submits for review immediately** (there is no "Ready to
publish" hold), treat it with more caution than the Play skill.

---

## One-time Setup

1. **Credentials.** In the Amazon Developer Console → **Tools & Services → API
   Access → App Submission API**, create a security profile and copy its **client
   id** and **client secret** (scope `appstore::apps:readwrite`). Grab the app's
   **App ID** (`amzn1.devportal.mobileapp...`) from the app's detail page.

2. **Store the secret outside the repo.** `client_secret` is a real secret, so keep
   the three exports in an untracked file (mirrors how `.env.asc` points at
   `~/.secrets` for the Apple `.p8` and the Play JSON):
   ```bash
   # ~/.secrets/amazon-appstore.env   (NOT in the repo)
   export AMAZON_CLIENT_ID="amzn1.application-oa2-client...."
   export AMAZON_CLIENT_SECRET="...."
   export AMAZON_APP_ID="amzn1.devportal.mobileapp...."
   ```
   `.env.asc` already sources it when present, so `source .env.asc` loads all three.

3. **Verify (read-only):**
   ```bash
   cd /Users/ace/Projects/mobile/clocks-in-motion
   source .env.asc && ~/.claude/skills/amazon-appstore/push-amazon.sh --check
   ```
   Confirms the LWA token works and the app is reachable, and reports whether an
   edit is already open. It makes **no** writes.

> **Prerequisite (external):** the App Submission API only *updates existing* apps.
> The app must have had a **first manual submission** in the Console before this
> skill (or the Fire TV CI job) can touch it.

---

## Usage

```bash
cd /Users/ace/Projects/mobile/clocks-in-motion
source .env.asc
SH=~/.claude/skills/amazon-appstore/push-amazon.sh

$SH --check                    # verify auth + app access (read-only)

# DRY-RUN by default — LOCAL preview, nothing sent to Amazon:
$SH all                        # text + screenshots + artwork + video, all locales
$SH listing                    # text + screenshots (no artwork/video)
$SH text it-IT                 # one locale, text only
$SH screenshots                # Fire TV + Fire Tablet image sets (replace)
$SH video                      # promo video → every locale (captionless)
$SH assets                     # icons + Fire TV artwork

# Create the UPCOMING VERSION (--stage): opens ONE edit, applies, leaves it OPEN
# and uncommitted. Review/Submit it in the Amazon Developer Console (the human gate):
$SH all --stage
$SH listing --stage

# Submit for review IMMEDIATELY (--commit) — no Console gate, use sparingly:
$SH all --commit
```

---

## What Gets Pushed

**Text** (per locale MD, first fenced block after the heading) → `PUT .../listings/{lang}`:

| MD heading | Amazon field | Type |
|---|---|---|
| `### Title` | `title` | string |
| `### Short description` | `shortDescription` | string (≤ 2000 bytes) |
| `### Long description` | `fullDescription` | string (≤ 4000 chars) |
| `### Product features` | `featureBullets` | array (one bullet per line, `•` stripped) |
| `### Search keywords` | `keywords` | **required** array of **single words** (see gotcha 5) |
| `## Release notes` / `### Release notes` | `recentChanges` | string |

Amazon has no PATCH, so the script **GETs the current listing, merges these fields,
and PUTs the whole object** with the `If-Match` ETag.

**Screenshots** (per locale, replace) from `android-native/screenshots/captioned/<sdir>/`:

| Capture | Amazon image type | Audience |
|---|---|---|
| `tv_*.png` | `firetv-screenshots` | Fire TV |
| `tablet_*.png` | `screenshots` | Fire Tablet (shared general slot) |

Phone screenshots are intentionally **not** pushed (Fire Phone is dead; there's no
separate tablet slug — Fire Tablets use the general `screenshots` set). Any `*cast*`
capture is auto-excluded in `_screenshot_files` — the Amazon build has no Google Cast.

**Promo video** (`video` / `all`) → the `videos` asset set of **every** locale. The
video has no captions, so one file serves all languages:
`store-listings/assets/video/clocks-in-motion-amazon-appstore-draft.mp4` (via
`VIDEO_DIR`/`VIDEO_FILE`). Uploaded as `video/mp4`; the returned object is just an
`id` (no thumbnail required). Verified live.

**Artwork** (default language `en-US`, single-image replace):
`large-icons` ← `assets/icon-512.png`, `small-icons` ← `assets/icon-114.png`,
`firetv-backgrounds` ← `assets/fire-tv-background-1920x1080.png`,
`firetv-icons` ← `assets/fire-tv-banner-1280x720.png` (the Fire TV store tile).

*Not pushed:* the APK (CI), and category/website/privacy/price (Console-managed).

---

## Locale mapping (`LOCALES`)

| Amazon listing | Metadata file | Screenshot dir |
|---|---|---|
| en-US | `metadata.md` | `en` |
| de-DE | `i18n/de.md` | `de` |
| fr-FR | `i18n/fr.md` | `fr` |
| es-ES | `i18n/es-ES.md` | `es-ES` |
| it-IT | `i18n/it.md` | `it` |
| ja-JP | `i18n/ja.md` | `ja` |

**No Simplified Chinese.** Verified live: Amazon does not offer Chinese as an
available listing language for this app — every code tried (`zh-Hans`, `zh-CN`,
`zh`, `cmn-Hans`, `zh-Hans-CN`) returns `400 Invalid language`, and `ko-KR` is
likewise unavailable, while `de-DE/fr-FR/es-ES/it-IT/ja-JP` (and `pt-BR`) return
`200`. So the repo's `i18n/zh-Hans.md` has no Amazon target and is omitted from
`LOCALES`. As of the live probe only **en-US was populated**; this skill fills the
five empty localized listings.

---

## Gotchas

1. **`--commit` submits for review immediately** — Amazon has no Managed-Publishing
   hold. Preview with a plain dry-run first; only `--commit` when the listing is final.
2. **One active edit at a time.** The script refuses to run `--commit` if an edit is
   already open in the Console (committing it would submit someone else's in-progress
   work). Resolve/discard the Console edit first.
3. **PUT replaces the whole listing.** Every field you don't send is dropped, so the
   script always GET-merges first. If you add a new MD field, make sure it's mapped.
4. **Fire TV only by default; no phone screenshots.** The Fire Phone is dead, so the
   general `screenshots` slot isn't worth a phone set — and the shared phone/tablet
   captures include a **Google Cast** screen (`*_06-cast-*.png`) this build lacks. The
   `tv_*` Fire TV set (1920×1080) is clean and is all that's pushed. Fire *Tablets*
   (alive) use the same general `screenshots` slot — there's no separate tablet slug —
   so to cover them, enable the `tablet_*.png` line in `SCREENSHOT_TYPES` after
   dropping `tablet_06-cast-*`.
5. **`keywords` is required and must be single words.** Amazon rejects multi-word
   phrases with `400 SINGLE_WORD`, and a missing `keywords` array fails
   `required_data_absent`. Our metadata stores comma-separated *phrases* (for
   Apple/Play), so the script splits them into deduped single words at push time —
   no source-file change needed.
6. **Image writes need the `If-Match` ETag too** (not just listing PUTs), and the
   ETag changes after every mutation — the script re-reads it before each
   upload/delete. Image sets return `{"images": [...]}`; single-image types
   (icons/backgrounds) are handled by delete-then-upload.
7. **Token expires hourly** → the script auto-refreshes on a 403 and retries.
8. **Transient 5xx/429** are retried with exponential backoff (image uploads especially).
9. **Verified live (2026-08-23), except commit.** Auth, the edit lifecycle, listing
   text PUTs (all fields incl. the arrays), screenshot upload/replace, asset replace,
   and every image-type slug were exercised against the real API via throwaway edits
   that were then discarded. Only `POST /edits/{id}/commit` is untested — because
   committing submits for review. Live en-US is already populated; the 5 localized
   listings are empty and are what a push fills.

---

## Key API calls

```
POST https://api.amazon.com/auth/o2/token   grant_type=client_credentials, scope=appstore::apps:readwrite
Base https://developer.amazon.com/api/appstore/v1/applications/{appId}
GET  /edits                                  # active edit + ETag  (also used by --check)
POST /edits                                  # create edit
GET  /edits/{editId}/listings/{lang}         # current listing + ETag (for merge)
PUT  /edits/{editId}/listings/{lang}         # If-Match ETag; full listing object
POST /edits/{editId}/listings/{lang}/{type}/upload    # image (octet-stream)
DELETE /edits/{editId}/listings/{lang}/{type}/{imgId} # If-Match ETag
POST /edits/{editId}/commit                  # If-Match ETag → status SUBMITTED
DELETE /edits/{editId}                        # discard (cleanup on failure)
```

Reference: https://developer.amazon.com/docs/app-submission-api/overview.html
