---
name: google-play
description: Use when pushing Google Play store-listing metadata (app name, short/full description) or screenshots/artwork to the Play Console for Clocks in Motion. Triggers on "push play metadata", "update Play Store listing", "upload play screenshots", "Google Play API", "androidpublisher".
---

# Google Play — Store Listing Push

## Overview

Pushes the Play **store listing** (title, short/full description) and images
(phone / 10-inch tablet / TV screenshots + icon, feature graphic, TV banner) via
the `androidpublisher` v3 API.

- Script:  `~/.claude/skills/google-play/push-play.py`
- Wrapper: `~/.claude/skills/google-play/push-play.sh` (runs it in the skill venv)

**Scope boundary:** this skill touches the *store listing only*. The AAB and
**release notes** are owned by CI (`android-native.yml` publishes the google-flavor
bundle to the internal track). This script never touches tracks, versions, or the
binary. See the sister skill `app-store-connect` for Apple.

---

## How Play differs from App Store Connect

| | App Store Connect | Google Play |
|---|---|---|
| Auth | JWT via `.p8` | **service-account JSON** → OAuth2 |
| Change model | per-resource PATCH | **edit transaction**: insert → change → validate → **commit** |
| Text fields | subtitle/desc/keywords/promo | **title (30), short (80), full (4000)** — no keywords/subtitle |
| "What's new" | on the version | **release notes on a track release** → left to CI |
| Locale codes | `it`, `ja` | `it-IT`, `ja-JP`, `zh-CN` |

---

## One-time Setup

1. **Venv + deps** (never `pip install` globally — PEP 668):
   ```bash
   python3 -m venv ~/.claude/skills/google-play/.venv
   ~/.claude/skills/google-play/.venv/bin/pip install google-api-python-client google-auth
   ```

2. **Service account key.** Reuse or create a service account with access to this
   app, download its **JSON** key, save it outside any repo (e.g.
   `~/.secrets/play-service-account.json`). In **Play Console → Users and
   permissions**, grant it **“Edit store listing, pricing & distribution”** for
   `com.caplaz.clock`. (The CI publisher account may only have release permission —
   listing edits need this extra grant.)

3. **Point at the key** (in `.env.asc` alongside the Apple vars, or your shell):
   ```bash
   export PLAY_SERVICE_ACCOUNT_JSON="$HOME/.secrets/play-service-account.json"
   ```
   The JSON is a secret — keep it gitignored (`*.json` under `~/.secrets`, never the repo).

4. **Verify:**
   ```bash
   cd /Users/ace/Projects/mobile/clocks-in-motion
   source .env.asc && ~/.claude/skills/google-play/push-play.sh --check
   ```
   Confirms the key is valid, the service account can open an edit, and lists the
   existing listing languages. It creates a throwaway edit and discards it.

---

## Usage

```bash
cd /Users/ace/Projects/mobile/clocks-in-motion
source .env.asc
SH=~/.claude/skills/google-play/push-play.sh

$SH --check                    # verify auth + edit access

# DRY-RUN by default (stages in an edit, validates, then discards — nothing changes):
$SH all                        # listing text + screenshots + artwork, all locales
$SH text                       # title/short/full description only
$SH screenshots de-DE          # one locale's screenshots
$SH assets                     # icon, feature graphic, TV banner

# Stage for real (add --commit):
$SH all --commit
```

### Commit vs Console publish

Default runs are **dry-run**: the edit is validated then thrown away. `--commit`
commits the edit. **Turn Managed Publishing ON** in Play Console so a commit only
*stages* the changes ("Ready to publish") — you then click **Publish** in the
Console. With Managed Publishing OFF, a commit publishes immediately.

> API edits are separate from the Console's own draft — you can't open an
> API-committed change as a Console "edit"; Managed Publishing is the human gate.

---

## What Gets Pushed

**Text** (from each locale's MD, first fenced block after the heading) → `edits.listings`:

| MD heading | Play field | Limit |
|---|---|---|
| `### App name` | `title` | 30 |
| `### Short description` | `shortDescription` | 80 |
| `### Full description` | `fullDescription` | 4000 |

**Promo video** — Play hosts no video files; the listing `video` field is a **YouTube
URL**. Upload the `.mp4` to YouTube, paste the watch URL into the en metadata's
`### Promo video` block, and `text`/`all` set it on every locale (skipped while the
block is a placeholder).

**Screenshots** (per locale, replaced) → `edits.images`, from
`android-native/screenshots/captioned/<sdir>/`:

| Capture | Play image type |
|---|---|
| `phone_*.png` | `phoneScreenshots` |
| `tablet_*.png` | `tenInchScreenshots` |
| `tv_*.png` | `tvScreenshots` |

**Artwork** (default language en-US) → `edits.images`: `icon` ← `assets/icon-512.png`,
`featureGraphic` ← `assets/feature-graphic-1024x500.png`, `tvBanner` ←
`assets/android-tv-banner-1280x720.png`.

*Not pushed:* release notes (CI), category/website/privacy URL (Console; some aren't
API-settable), and any App content declaration.

---

## Locale mapping (three naming schemes bridged in `LOCALES`)

| Play listing | Metadata file | Screenshot dir |
|---|---|---|
| en-US | `metadata.md` | `en` |
| de-DE | `i18n/de.md` | `de` |
| fr-FR | `i18n/fr.md` | `fr` |
| es-ES | `i18n/es-ES.md` | `es-ES` |
| it-IT | `i18n/it.md` | `it` |
| ja-JP | `i18n/ja.md` | `ja` |
| **zh-CN** | `i18n/zh-CN.md` | **`zh-Hans`** |

Note the Simplified-Chinese mismatch: the listing is `zh-CN`, the metadata file is
`zh-CN.md`, but the Android pipeline emits a `zh-Hans` screenshot folder.

---

## Gotchas

1. **App content declarations block commits.** Health, Data safety, Ads, Target
   audience are app-level, set only in the Console, and block ALL commits (API and
   CI) until complete. Not settable via the API. If a commit is rejected for this,
   finish the declaration in the Console.
2. **Listing-edit permission is separate** from release/track permission — grant it
   explicitly to the service account.
3. **Managed Publishing** is the human gate (see above); without it, `--commit` is live.
4. **Locale codes carry regions** (`it-IT`, `ja-JP`) and Simplified Chinese is `zh-CN`.
5. Never `pip install` globally (PEP 668) — use the venv; the `.sh` wrapper enforces it.
6. **Image uploads transiently `500`.** Pushing ~126 images per full run makes a
   flaky `500 Internal error` likely; every API call uses `.execute(num_retries=5)`
   (exponential backoff) so a single blip self-heals. A run that still fails after
   retries discards its edit — just re-run.

---

## Key API calls

```
edits.insert / validate / commit / delete   (the transaction)
edits.listings.update(packageName, editId, language, body={title, shortDescription, fullDescription})
edits.images.deleteall(..., language, imageType)
edits.images.upload(..., language, imageType, media_body)
edits.listings.list(..., editId)            # used by --check
```

Reference: https://developers.google.com/android-publisher/api-ref/rest/v3/edits
