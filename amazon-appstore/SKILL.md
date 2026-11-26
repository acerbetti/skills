---
name: amazon-appstore
description: Use when pushing Amazon Appstore listing metadata (title, descriptions, feature bullets, keywords, release notes) or Fire TV / phone screenshots + artwork to the Amazon Developer Console for any app. Triggers on "push amazon metadata", "update Amazon Appstore listing", "upload Fire TV screenshots", "Amazon App Submission API".
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

**The locale set is discovered from the project, not hardcoded.** Add a language by
adding `i18n/<locale>.md` and a screenshot folder — no code change needed. See
"Locale mapping" below.

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

**Artwork** (uploaded to **every locale** — same files for each, language-agnostic):
`large-icons` ← `assets/icon-512.png`, `small-icons` ← `assets/icon-114.png`,
`firetv-backgrounds` ← `assets/fire-tv-background-1920x1080.png`,
`firetv-icons` ← `assets/fire-tv-banner-1280x720.png` (the Fire TV store tile).
Amazon requires icons and Fire TV artwork on each locale listing independently — they
do not inherit from `en-US`.

*Not pushed:* the APK (CI), and category/website/privacy/price/language-support (Console-managed).

---

## Locale mapping (discovered from disk, not hardcoded)

The locale set is **discovered from the project's files**, so adding a language
needs no code change. Three naming schemes meet here:

| | What it is | Example |
|---|---|---|
| **Amazon listing language** | what the API wants | `en-US`, `it-IT` |
| **Metadata file** | `store-listings/amazon-appstore/i18n/<local>.md` | `i18n/it.md` |
| **Screenshot dir** | `android-native/screenshots/captioned/<local>/` | `captioned/it/` |

Discovery reads the metadata files and the screenshot folders, then maps each
local code to an Amazon language. The mapping comes from the app's existing
listing languages when credentials are available, with a small static fallback
for the common cases:

| local | Amazon | note |
|---|---|---|
| en | en-US | the primary locale; its file is `metadata.md` |
| it | it-IT | |
| ja | ja-JP | |
| de, fr, es-ES | de-DE, fr-FR, es-ES | same code, region added |

Anything else follows the same pattern: the local code is looked up in the app's
listing languages, and its region-qualified identifier is used (`nl` → `nl-NL`,
`sv` → `sv-SE`). If a local code isn't in Amazon's list, the code is used as-is
and Amazon rejects it with a clear error.

Amazon has no "list all languages" endpoint, so the skill probes the candidate
languages against an open edit when one exists. Opening an edit is a server
write, so with no open edit the skill uses its static aliases instead and lets
Amazon's `400 Invalid language` be the authority when a language is pushed.

### Adding a language

No code change needed. For locale `nl`:

1. Write `store-listings/amazon-appstore/i18n/nl.md` with `### Title`,
   `### Short description`, `### Long description`, `### Product features`,
   `### Search keywords` and `### Release notes` sections.
2. Add screenshots in `android-native/screenshots/captioned/nl/`, named to match
   the configured globs (`tv_*.png`, `tablet_*.png`).
3. Run the push. The script discovers `nl` from disk and pushes it.

The locale argument accepts both forms — `it-IT` (the Amazon language) or `it`
(the local code). Unknown locales are rejected, not silently ignored.

### Amazon's available languages are per-app

Amazon does not offer every language to every app, and the set can change.
Verified live against the Clock in Motion app: `en-US/de-DE/fr-FR/es-ES/it-IT/ja-JP`
(and `pt-BR`) return `200`, while Simplified Chinese (`zh-Hans`, `zh-CN`, `zh`,
`cmn-Hans`, `zh-Hans-CN`) and `ko-KR` return `400 Invalid language`. So that
project's `i18n/zh-Hans.md` has no Amazon target.

If a language comes back as `400 Invalid language`, that's Amazon's answer — the
file is fine, the app just isn't offered that language. Leave it out of the push
rather than working around it.

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
9. **DRM cannot be set via the API — set it to Yes in the Console.** Amazon's
   `appstore/v1` API has no endpoint for Binary File Options (DRM). `PUT /apks/{id}`,
   `PUT /apks/{id}/targeting`, and `PUT /edits/{id}` all return `400 Unable to fetch
   the request scope` for DRM fields. Targeting PUT silently ignores unknown fields.
   The commit fails with `error_apk_drm_value_missing` for any APK uploaded via API.
   **Workaround**: CI uploads the APK and leaves the edit open; the release manager
   opens the Amazon Developer Console → open edit → Binary File Options → **set DRM
   to Yes** (the `amazon` flavor includes `com.amazon.device:amazon-appstore-sdk`,
   so the APK is DRM-capable) → Submit. The SDK is zero-config — no initialization
   code needed; it self-registers via a ContentProvider merged from its manifest.
10. **Verified live (2026-08-25), except commit.** Auth, the edit lifecycle, listing
    text PUTs (all fields incl. the arrays), screenshot upload/replace, asset replace,
    and every image-type slug were exercised. The commit path requires the Console step
    above for DRM. Live en-US was already populated; the 5 localized listings are filled
    by this skill.
12. **"Reduced Device Support" warning = unselected device targeting, not the manifest.**
    An APK uploaded via the API auto-targets almost nothing (Amazon selected 2 of 134
    devices in practice), so the Console warns that device support dropped vs the
    previous (manually-submitted) version. It is NOT caused by the manifest, the
    Amazon SDK, or DRM — byte-diffing two APKs confirmed identical native libs and an
    identical (actually more-permissive) manifest. **Fix:** `GET
    /edits/{id}/apks/{apkId}/targeting` → `{amazonDevices:[{id,name,status,reason}]}`;
    flip every device with `status=NOT_TARGETING` and an empty `reason` to `TARGETING`,
    then `PUT` it back with the **per-APK targeting ETag** (from that GET — a distinct
    ETag from the apk/listing/edit ones). Leave `DISABLED` devices alone (they carry a
    real reason, e.g. `minSdkVersion=26` excluding Fire OS 5/6). This is owned by CI
    (the APK uploader), automated in `android-native.yml`'s publish-amazon step.
11. **Language Support cannot be set via the API — set it manually in the Console.**
    `PUT /edits/{id}/details` silently ignores any unknown fields (e.g. `supportedLanguages`,
    `languages`); there is no `/languages` or `/locales` sub-resource in v1. The Language
    Support checkboxes (de, es, fr, it, ja in addition to en) must be set manually:
    Console → open edit → **App Information → Language Support** → check all languages
    the app UI supports. This is separate from the store listing languages which this
    skill pushes. Amazon does **not** auto-derive this from the APK's `localeConfig`.

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
