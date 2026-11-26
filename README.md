# Skills

Claude Code skills for publishing mobile apps to the three major app stores.

Each skill is a self-contained directory with a Python script, a thin shell wrapper, and a `SKILL.md` that Claude loads when the skill is invoked.

**Locales are discovered from your project, not hardcoded.** Every skill reads the
metadata files and screenshot folders it finds on disk, then maps each language to the
store's own code. Adding a language means adding a file — no code change in any skill.

## Skills

### `app-store-connect/`

Pushes metadata and assets to **Apple App Store Connect** (iOS, macOS, tvOS) via the ASC REST API.

- Localized text: description, keywords, promotional text, what's new, subtitle, privacy policy
- Localized screenshots (per-locale captioned PNGs)
- App preview videos (locale-agnostic; same file per device to every locale)
- Any number of locales, across all 3 Apple platforms under a single App ID

**Auth:** JWT signed with an App Store Connect API key (`.p8`).  
**Deps:** venv with `PyJWT`, `requests`, `cryptography` (Python 3.10+).

### `google-play/`

Pushes the store listing to **Google Play** via `androidpublisher` v3.

- Localized text: title, short description, full description
- Promo video (YouTube URL, same for every locale)
- Phone, 10-inch tablet, and TV screenshots
- Icon, feature graphic, TV banner

**Auth:** service-account JSON → OAuth2.  
**Deps:** venv with `google-api-python-client`, `google-auth`.

### `amazon-appstore/`

Pushes the store listing to the **Amazon Appstore** via the App Store Submission API.

- Localized text: title, short/long description, feature bullets, search keywords, release notes
- Fire TV screenshots and Fire Tablet screenshots (Cast captures auto-excluded)
- Icons, Fire TV background and banner artwork
- Promo video (locale-agnostic; same file to every locale)
- Any number of locales (Amazon offers a per-app language set)

**Auth:** LWA `client_credentials` → bearer token.  
**Deps:** none — pure Python stdlib.

## Usage

Each skill's `SKILL.md` has full setup and usage instructions. The short version:

```bash
# App Store Connect
source .env.asc
~/.claude/skills/app-store-connect/push-metadata.sh --check
~/.claude/skills/app-store-connect/push-metadata.sh all
~/.claude/skills/app-store-connect/push-metadata.sh screenshots tvos

# Google Play
source .env.asc
~/.claude/skills/google-play/push-play.sh --check
~/.claude/skills/google-play/push-play.sh all --commit

# Amazon Appstore
source .env.asc
~/.claude/skills/amazon-appstore/push-amazon.sh --check
~/.claude/skills/amazon-appstore/push-amazon.sh all --stage    # stage for review
~/.claude/skills/amazon-appstore/push-amazon.sh all --commit   # submit for review
```

All three scripts are **dry-run by default** (or local-preview-only for Amazon). Pass `--commit` to write to the store. Amazon also supports `--stage` to leave an edit open for manual review in the Developer Console, and `--amend` to add assets to an already-open edit.

## Languages

Every skill discovers its languages from your project. There is no hardcoded locale
list to edit when you add one.

Each skill looks for:

- `store-listings/<store>/metadata.md` — the primary language (`en-US`)
- `store-listings/<store>/i18n/<locale>.md` — one file per additional language
- `android-native/screenshots/captioned/<locale>/` (Play, Amazon) or
  `apple-native/screenshots/captioned/<locale>/` (Apple) — screenshots per language

The `<locale>` in the file name is the **local code** (`en`, `it`, `ja`, `zh-Hans`).
Each skill maps that to the store's own language code (`en-US`, `it-IT`, `ja-JP`,
`zh-CN`) using the store's live language list where it can, with a static fallback
for the common cases. If a language isn't recognised, the store rejects it with a
clear error rather than the skill silently skipping it.

So adding a language is three steps and no code change:

1. Write the metadata file (`i18n/nl.md`).
2. Capture the screenshots into the locale folder.
3. Push.

Each skill's `--check` prints the languages it discovered, so you can confirm
before pushing.

## Credentials

No secrets are stored in this repo. Each skill reads credentials from environment variables, which you source from a local `.env` file before running.

| Store | Env vars | Key file |
|---|---|---|
| App Store Connect | `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_PRIVATE_KEY_PATH`, `ASC_APP_ID` | `~/.secrets/AuthKey_<KEYID>.p8` |
| Google Play | `PLAY_SERVICE_ACCOUNT_JSON` | `~/.secrets/play-service-account.json` |
| Amazon Appstore | `AMAZON_CLIENT_ID`, `AMAZON_CLIENT_SECRET`, `AMAZON_APP_ID` | — |

## Workflow skills

General-purpose development skills, not tied to a single store.

- `code-hygiene/`: end-of-day review of the day's changes for regressions, best-practice violations, and dead code.
- `plan-review-loop/`: iterative adversarial review of a plan file with Codex, fixing blockers until the plan passes. Requires the Codex plugin.
- `seo-audit/`: SEO audit for websites, covering technical SEO, on-page SEO, and content quality.

## Third-party notices

- `plan-review-loop/` calls the Codex companion script from [openai/codex-plugin-cc](https://github.com/openai/codex-plugin-cc), Copyright 2026 OpenAI, licensed under the [Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0). That plugin is not vendored in this repo; install it separately as described in `plan-review-loop/SKILL.md`.

## License

The skills in this repo are released under the [MIT License](LICENSE). The third-party Codex plugin noted above keeps its own Apache 2.0 license.
