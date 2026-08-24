---
name: app-store-connect
description: Use when pushing App Store metadata, descriptions, keywords, promotional text, what's new, subtitle, privacy policy, or screenshots to App Store Connect for Clocks in Motion. Triggers on "push metadata", "update App Store", "upload metadata", "upload screenshots", "ASC API", "App Store Connect".
---

# App Store Connect — Metadata & Screenshot Push

## Overview

Pushes localized metadata (from `store-listings/apple-app-store/`) and localized
screenshots (from `apple-native/screenshots/captioned/<locale>/`) to App Store
Connect via the REST API. Covers iOS, macOS, and tvOS; all 7 locales.

- Script:  `~/.claude/skills/app-store-connect/push-metadata.py`
- Wrapper: `~/.claude/skills/app-store-connect/push-metadata.sh` (runs the script in the skill's venv)

> **Read "Gotchas" below before your first push.** Several ASC behaviours are
> non-obvious and have bitten us: one App ID for all platforms, subtitle/privacy
> being app-wide and lockable, and screenshot captions being baked into the image.

---

## One-time Setup

### Step 1 — Create the Python venv (deps can't install system-wide)

Homebrew Python is "externally managed" (PEP 668), and `pip` isn't on the PATH.
Do **not** `pip install` globally — create a venv scoped to the skill:

```bash
python3 -m venv ~/.claude/skills/app-store-connect/.venv
~/.claude/skills/app-store-connect/.venv/bin/pip install PyJWT requests cryptography
```

`push-metadata.sh` uses this venv automatically. If it's missing, the wrapper
prints the exact commands to create it.

---

### Step 2 — Create an App Store Connect API key

1. Open [App Store Connect → Users and Access → Integrations](https://appstoreconnect.apple.com/access/api)
2. Under **App Store Connect API**, click **+** (Generate API Key)
3. **Name**: anything; **Access**: **App Manager** (minimum; Admin also works)
4. **Download the `.p8` immediately** — it can only be downloaded once
5. Save it outside any repo, e.g. `~/.secrets/AuthKey_<KEYID>.p8`
6. Note the **Key ID** (10 chars, next to the key) and **Issuer ID** (UUID at top)

The `.p8` filename should contain the Key ID, e.g. `AuthKey_TS9Y7X775S.p8`.

---

### Step 3 — Find the App ID (ONE id for all platforms)

iOS, macOS, and tvOS are **not separate apps** — they are platforms under a single
app record with **one** numeric App ID. Open the app in ASC and read it from the URL:

```
https://appstoreconnect.apple.com/apps/1493995311/appstore/...
                                       ^^^^^^^^^^
                                       ASC_APP_ID  (same for iOS/macOS/tvOS)
```

The script filters by `filter[platform]` to reach each platform's version.

---

### Step 4 — Configure credentials in `.env.asc` (committed to the repo)

Credentials live in `.env.asc` at the project root. **The Key ID, Issuer ID, and
App ID are not secret** (the App ID appears in public store URLs) — only the `.p8`
is. So `.env.asc` is committed; `*.p8` is gitignored.

```bash
# .env.asc  (project root)
export ASC_KEY_ID="TS9Y7X775S"
export ASC_ISSUER_ID="69a6de88-dbcb-47e3-e053-5b8c7c11a4d1"
export ASC_PRIVATE_KEY_PATH="$HOME/.secrets/AuthKey_TS9Y7X775S.p8"
export ASC_APP_ID="1493995311"
```

Source it before every run: `source .env.asc && …` (examples below do this).

---

### Step 5 — Verify auth before pushing

**Always run this first:**

```bash
cd /Users/ace/Projects/mobile/clocks-in-motion
source .env.asc && ~/.claude/skills/app-store-connect/push-metadata.sh --check
```

It validates: the 4 env vars are set → the `.p8` exists and is a valid PEM →
a JWT can be signed → the live API accepts it → `ASC_APP_ID` is visible to the key.

```
── Environment variables ──
  ✓ ASC_KEY_ID = TS9Y7X…
  ✓ ASC_ISSUER_ID = 69a6de…
  ✓ ASC_PRIVATE_KEY_PATH = /Users…
  ✓ ASC_APP_ID = 1493995311
── Private key file ──   ✓ … exists and looks valid
── JWT token ──         ✓ Token generated (eyJhbGciOiJFUzI1NiIsI…)
── Live API call ──     ✓ Auth OK — N app(s) visible: … Clocks in Motion (id=1493995311, …)
── App ID check ──      ✓ ASC_APP_ID (1493995311) is visible
✓ Auth check passed. You're ready to push metadata.
```

---

## Usage

Run from the project root; `source .env.asc` first so credentials are in the env.

```bash
cd /Users/ace/Projects/mobile/clocks-in-motion
source .env.asc

SH=~/.claude/skills/app-store-connect/push-metadata.sh

$SH --check                 # verify auth (do this first)

# Text metadata (per-platform version fields) + shared app info (subtitle/privacy):
$SH all                     # every platform, every locale
$SH ios                     # one platform, all locales
$SH tvos ja                 # one platform, one locale

$SH tvos --screenshots      # text + screenshots in one go
$SH appinfo                 # ONLY the shared subtitle + privacy (all locales)
$SH appinfo de              # ONLY shared fields, one locale

# Screenshots only (replace; each locale gets its OWN captioned images):
$SH screenshots tvos        # one platform, all locales
$SH screenshots all         # every platform
$SH screenshots tvos de     # one platform, one locale

# App-preview VIDEOS (same captionless file → every locale). Needs an editable
# (PREPARE_FOR_SUBMISSION) version — i.e. a new version, not a live one:
$SH previews tvos           # one platform, all locales
$SH previews all            # every platform
$SH tvos --previews         # text + previews together
```

Overrides: `ASC_META_DIR` (metadata dir), `ASC_SCREENSHOT_TEMPLATE` (screenshot
source; must contain `{locale}`).

---

## What Gets Pushed

**Per-platform** — from each `## iOS & iPadOS` / `## macOS` / `## tvOS` section →
`AppStoreVersionLocalization` (per platform, per locale):

| MD heading | API field |
|---|---|
| `### Description` | `description` |
| `### Keywords` | `keywords` |
| `### Promotional Text` | `promotionalText` |
| `### What's New` | `whatsNew` |

Also on `AppStoreVersionLocalization` — ASC does **not** inherit these across
locales, so each localization must set them explicitly or they show up blank:

| MD heading | Source | API field |
|---|---|---|
| `### Marketing URL` | **each locale's own `## Common`** (localized landing page, e.g. `…/de/`) | `marketingUrl` |
| `### Support URL` | the **en `## Common`** (one shared value for all) | `supportUrl` |

**App-wide** — from the `## Common` section → `AppInfoLocalization`. ASC stores
these **once per app, not per platform**, so they're pushed once (via `appinfo`,
or automatically after any text push):

| MD heading | API field |
|---|---|
| `### Subtitle` | `subtitle` |
| `### Privacy Policy Text` | `privacyPolicyText` |

**Screenshots** — per locale, from `apple-native/screenshots/captioned/<locale>/`.
Captions are baked into each image, so every language has its own files. The push
**replaces** (deletes then re-uploads) and sets display order by filename.

**App previews (video)** — per platform → `appPreviewSets`/`appPreviews` (reserve →
chunk-upload → commit, exactly like screenshots). Videos are **locale-agnostic** (no
captions), so the same per-device file from `store-listings/assets/video/` is uploaded
to **every** locale. `previewType`: `IPHONE_67`, `IPAD_PRO_3GEN_129`, `DESKTOP`,
`APPLE_TV` (no `APP_` prefix, unlike screenshot display types). Requires an **editable
version** (there was none when this was added — 3.1.0 is live), so it's implemented but
awaits a new version to run against.

---

## Gotchas & Lessons Learned

1. **One App ID for all platforms.** iOS/macOS/tvOS share a single app record and
   `ASC_APP_ID`. There is no per-platform App ID.

2. **Subtitle & privacy-policy text are app-wide.** They live in the shared
   `AppInfo`, not per platform — you *cannot* have a different subtitle per
   platform. Keep them in `## Common`, not in a platform section.

3. **Only `PENDING_DEVELOPER_RELEASE` (approved, awaiting release) locks the shared
   AppInfo.** Editing subtitle/privacy then returns `409 INVALID_STATE` ("field
   'subtitle' can not be modified in the current state") — release or reject that
   version first. **`WAITING_FOR_REVIEW` is editable** (metadata can be revised
   while in review), so a version sitting in review does *not* block a subtitle
   change; the edit rides along with the review.

4. **Version-level fields stay editable independently.** Description/keywords/
   promo/what's-new **and screenshots** for a platform whose version is still in
   `PREPARE_FOR_SUBMISSION` remain editable even while the shared AppInfo is locked
   by *another* platform's approved version. (That's how we updated tvOS while
   iOS/macOS were locked.)

5. **Screenshot captions are baked into the PNG.** Uploading one flat English
   folder to every locale bakes the *English* caption onto all store pages — the
   original bug. Always source **per-locale** from
   `apple-native/screenshots/captioned/<locale>/` (single source of truth; app-source
   artwork stays with the app project, not duplicated under `store-listings/`).

6. **Uploads are idempotent by replacement.** `screenshots` deletes the existing
   set then re-uploads, so re-running never duplicates (an early run left 6 in
   en-US by appending twice — replacement fixed it).

7. **Italian's ASC locale is `it`, not `it-IT`.** Creating `it-IT` returns
   `409 "The language specified is not listed for localization"`.

8. **"I don't see the screenshots" is usually a UI issue, not an upload issue.**
   In the ASC web UI the screenshots section shows one language at a time: pick the
   correct **platform** in the sidebar (tvOS is separate from iOS/macOS), switch the
   **language dropdown**, and hard-refresh (⌘⇧R) — API-created localizations
   sometimes don't populate the dropdown until then. Verify via the API instead
   (see below).

9. **Python env:** never `pip install` globally (PEP 668). Use the skill venv; the
   `.sh` wrapper enforces it.

10. **A fresh version starts with only the primary locale (en-US).** A text push
    auto-creates the missing version localizations before setting their fields, so
    `push-metadata.py <platform>` works on a brand-new version. (Screenshots still
    skip a locale with no version localization — push text first.)

11. **Bulk 4K uploads transiently `5xx`.** Pushing many screenshots per run can hit
    a flaky `500/503`; every request (incl. the CDN `PUT`) retries with exponential
    backoff via `_request`. A run that still fails after retries changes nothing new
    on re-run (screenshots use replace semantics) — just re-run.

---

## Verifying an upload (source of truth = the API)

The web UI can mislead; the API cannot. To prove screenshots exist per locale and
are real images, list them and resolve the CDN URL from `imageAsset.templateUrl`:

```
GET /appStoreVersions/{versionId}/appStoreVersionLocalizations
GET /appStoreVersionLocalizations/{locId}/appScreenshotSets
GET /appScreenshotSets/{setId}/appScreenshots
# each screenshot: attributes.assetDeliveryState.state == COMPLETE, imageAsset != null
# view: templateUrl.replace({w},{h},{f}) → a live mzstatic.com image URL
```

A distinct `imageAsset` URL per locale = each language really has its own upload.

---

## Version / AppInfo editable states

| appStoreState | Version fields + screenshots | Shared AppInfo (subtitle/privacy) |
|---|---|---|
| `PREPARE_FOR_SUBMISSION` | ✅ editable | ✅ editable (if no other version is locked) |
| `WAITING_FOR_REVIEW` | ✅ editable (revisable in review) | ✅ editable |
| `PENDING_DEVELOPER_RELEASE` | 🔒 locked | 🔒 **locks the shared AppInfo** |
| `READY_FOR_SALE` | 🔒 (live) | 🔒 (live) |

The script targets the version in `PREPARE_FOR_SUBMISSION` and errors clearly if
there isn't one, or if the AppInfo is locked.

---

## Mappings

**Platform → MD section:** `ios`→`## iOS & iPadOS`, `macos`→`## macOS`, `tvos`→`## tvOS`.

**Source files:** `en` → `store-listings/apple-app-store/metadata.md`; others →
`store-listings/apple-app-store/i18n/<locale>.md`. Subtitle + privacy come from the
`## Common` section of each file.

**Locale codes** (local → ASC):

| en | de | fr | es-ES | it | ja | zh-Hans |
|---|---|---|---|---|---|---|
| en-US | de-DE | fr-FR | es-ES | **it** | ja | zh-Hans |

---

## Auth Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `zsh: command not found: pip` | pip not on PATH | Use the venv (Step 1); call `python3 -m pip` if needed |
| `error: externally-managed-environment` | PEP 668 blocks global installs | Install into the skill venv, not system Python |
| `ASC_KEY_ID is NOT set` | `.env.asc` not sourced | `source .env.asc` first |
| `File not found: …p8` | Wrong path | Fix `ASC_PRIVATE_KEY_PATH`; ensure the `.p8` is where it points |
| `does not look like a valid .p8` | Wrong file | Must be the ASC `.p8`, not a certificate |
| `Failed to generate token` | Key ID mismatch | `ASC_KEY_ID` must match the key that made the `.p8` |
| `401 Unauthorized` | Bad Issuer ID / revoked key | Copy Issuer ID exactly; revoked keys need a new `.p8` |
| `403 Forbidden` | Key role too low | Recreate the key as **App Manager**/**Admin** |
| `409 INVALID_STATE` on subtitle | Shared AppInfo locked | Release/reject the approved version (see Gotcha 3) |
| `409 language not listed` | Wrong locale code | Use `it`, not `it-IT` (Gotcha 7) |
| `No editable AppInfo` | No version in an editable state | Create/keep a version in Prepare for Submission |

**Key revoked or lost?** Generate a new one (Step 2); the old `.p8` can't be recovered.

---

## Key API Endpoints

```
GET   /v1/apps
GET   /v1/apps/{appId}/appStoreVersions?filter[platform]=IOS&filter[appStoreState]=PREPARE_FOR_SUBMISSION
GET   /v1/appStoreVersions/{versionId}/appStoreVersionLocalizations
PATCH /v1/appStoreVersionLocalizations/{locId}
GET   /v1/apps/{appId}/appInfos
GET   /v1/appInfos/{appInfoId}/appInfoLocalizations
PATCH /v1/appInfoLocalizations/{locId}
POST  /v1/appScreenshotSets   ·   POST/PATCH/DELETE /v1/appScreenshots
```

Full reference: https://developer.apple.com/documentation/appstoreconnectapi
