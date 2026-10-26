# Skills

Claude Code skills for publishing mobile apps to the three major app stores.

Each skill is a self-contained directory with a Python script, a thin shell wrapper, and a `SKILL.md` that Claude loads when the skill is invoked.

## Skills

### `app-store-connect/`

Pushes metadata and assets to **Apple App Store Connect** (iOS, macOS, tvOS) via the ASC REST API.

- Localized text: description, keywords, promotional text, what's new, subtitle, privacy policy
- Localized screenshots (per-locale captioned PNGs)
- App preview videos (locale-agnostic; same file per device to every locale)
- Covers all 7 locales and all 3 Apple platforms under a single App ID

**Auth:** JWT signed with an App Store Connect API key (`.p8`).  
**Deps:** venv with `PyJWT`, `requests`, `cryptography`.

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
- Covers 6 locales (en-US, de-DE, fr-FR, es-ES, it-IT, ja-JP)

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
