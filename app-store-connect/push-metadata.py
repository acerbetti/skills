#!/usr/bin/env python3
"""
push-metadata.py — Push App Store Connect metadata from local MD files.

Usage:
  python3 push-metadata.py --check                    # verify auth before pushing
  python3 push-metadata.py ios [locale]               # platform text + shared app info
  python3 push-metadata.py tvos [locale] --screenshots # + screenshots
  python3 push-metadata.py all                        # all platforms + locales
  python3 push-metadata.py appinfo [locale]           # shared subtitle + privacy only
  python3 push-metadata.py screenshots tvos [locale]  # screenshots only (replace)
  python3 push-metadata.py screenshots all            # screenshots, all platforms

Per-platform fields (description, keywords, promotional text, what's new) come from
each platform section. Subtitle + privacy policy are app-wide in App Store Connect
(one shared AppInfo), so they're read from the '## Common' section and pushed once.
Note: an approved-but-unreleased version (PENDING_DEVELOPER_RELEASE) locks the shared
AppInfo, so subtitle/privacy can't change until it ships.

Screenshots are replaced (not appended) and pushed to every locale so each
localized store page shows them explicitly. Filename order (NN_ prefix) = display order.

Required env vars:
  ASC_KEY_ID           API key ID (Users & Access → Integrations)
  ASC_ISSUER_ID        Issuer ID (same page)
  ASC_PRIVATE_KEY_PATH Path to downloaded .p8 private key file
  ASC_APP_ID           Numeric app ID (same for all platforms — from App Store Connect URL)

Install deps:  pip install PyJWT requests cryptography

Always run --check first to confirm auth is working before pushing.
"""

import os, re, sys, time, json, glob, hashlib
import jwt
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BUNDLE_ID = "com.caplaz.clock"
ASC_BASE  = "https://api.appstoreconnect.apple.com/v1"

# Resolve metadata dir from CWD so the script works when run from any project root.
# Override with ASC_META_DIR env var if your layout differs.
META_DIR = os.environ.get(
    "ASC_META_DIR",
    os.path.join(os.getcwd(), "store-listings", "apple-app-store"),
)

# Locale codes used in our MD files → App Store Connect locale identifiers
LOCALE_MAP = {
    "en":      "en-US",
    "de":      "de-DE",
    "fr":      "fr-FR",
    "es-ES":   "es-ES",
    "it":      "it",
    "ja":      "ja",
    "zh-Hans": "zh-Hans",
}

# Platform arg → (ASC platform string, MD section heading)
# All platforms share one App ID — iOS/macOS/tvOS are versions under the same app record.
PLATFORM_CFG = {
    "ios":   ("IOS",    "iOS & iPadOS"),
    "macos": ("MAC_OS", "macOS"),
    "tvos":  ("TV_OS",  "tvOS"),
}

# Localized screenshots live in per-locale folders (captions are baked into the
# image by add-captions.py, so each language needs its own files). This is the
# single source of truth — generated app screenshots stay with the app project,
# not duplicated under store-listings. The {locale} placeholder uses the LOCAL
# codes (en, de, fr, es-ES, it, ja, zh-Hans). Override with ASC_SCREENSHOT_TEMPLATE.
SCREENSHOT_TEMPLATE = os.environ.get(
    "ASC_SCREENSHOT_TEMPLATE",
    os.path.join(os.getcwd(), "apple-native", "screenshots", "captioned", "{locale}"),
)

# Platform arg → list of (ASC screenshotDisplayType, filename glob).
# Files are sorted by name, so the NN_ prefix controls display order.
SCREENSHOT_CFG = {
    "ios": [
        ("APP_IPHONE_67",         "ios-iphone-pro-max_*.png"),
        ("APP_IPAD_PRO_3GEN_129", "ios-ipad-pro-13_*.png"),
    ],
    "macos": [
        ("APP_DESKTOP", "macos_*.png"),
    ],
    "tvos": [
        ("APP_APPLE_TV", "tvos-4k_*.png"),
    ],
}

# App-preview videos. Unlike screenshots these are locale-AGNOSTIC (no captions in
# them), so ONE file per device is uploaded to EVERY locale, from a shared folder.
# NB: appPreviewSets.previewType has NO "APP_" prefix (unlike screenshotDisplayType).
VIDEO_DIR = os.environ.get(
    "ASC_VIDEO_DIR",
    os.path.join(os.getcwd(), "store-listings", "assets", "video"),
)
PREVIEW_CFG = {
    "ios": [
        ("IPHONE_67",         "clocks-in-motion-apple-iphone-draft.mp4"),  # 886×1920
        ("IPAD_PRO_3GEN_129", "clocks-in-motion-apple-ipad-draft.mp4"),    # 1200×1600
    ],
    "macos": [("DESKTOP",  "clocks-in-motion-apple-macos.mp4")],           # 1920×1080
    "tvos":  [("APPLE_TV", "clocks-in-motion-apple-tvos-draft.mp4")],      # 1920×1080
}

# Per-locale fields in AppStoreVersionLocalization (from the platform section)
VERSION_LOC_FIELDS = {
    "Description":      "description",
    "Keywords":         "keywords",
    "Promotional Text": "promotionalText",
    "What's New":       "whatsNew",
}

# Also AppStoreVersionLocalization fields — ASC does not inherit these across
# locales, so each localization must set them explicitly or they show up blank.
#   marketingUrl → localized landing page, read from EACH locale's own '## Common'
#   supportUrl   → one shared value (contact page), read once from the en '## Common'
LOCALIZED_URL_FIELDS = {"Marketing URL": "marketingUrl"}
GLOBAL_URL_FIELDS    = {"Support URL":   "supportUrl"}

# Fields in AppInfoLocalization (shared across platforms; read from '## Common')
INFO_LOC_FIELDS = {
    "Subtitle":            "subtitle",
    "Privacy Policy Text": "privacyPolicyText",
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _make_token() -> str:
    key_id     = os.environ["ASC_KEY_ID"]
    issuer_id  = os.environ["ASC_ISSUER_ID"]
    key_path   = os.environ["ASC_PRIVATE_KEY_PATH"]
    private_key = open(key_path).read()
    now = int(time.time())
    payload = {
        "iss": issuer_id,
        "iat": now,
        "exp": now + 1200,
        "aud": "appstoreconnect-v1",
    }
    return jwt.encode(payload, private_key, algorithm="ES256",
                      headers={"kid": key_id})

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

_TOKEN = None

def _headers() -> dict:
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = _make_token()
    return {"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"}

_RETRY_STATUS = {429, 500, 502, 503, 504}

def _request(method: str, url: str, **kw) -> requests.Response:
    """HTTP with exponential-backoff retry on transient 5xx/429/connection errors.

    Bulk 4K screenshot uploads occasionally hit a transient 500/503 (the Play
    skill saw the same); one blip shouldn't abort a whole run.
    """
    r = None
    for attempt in range(5):
        try:
            r = requests.request(method, url, **kw)
            if r.status_code not in _RETRY_STATUS:
                return r
        except requests.exceptions.RequestException:
            if attempt == 4:
                raise
        if attempt < 4:
            time.sleep(2 ** attempt)  # 1, 2, 4, 8s
    return r

def _get(path: str, params: dict = None) -> dict:
    r = _request("GET", f"{ASC_BASE}{path}", headers=_headers(), params=params)
    r.raise_for_status()
    return r.json()

def _patch(path: str, body: dict) -> dict:
    r = _request("PATCH", f"{ASC_BASE}{path}", headers=_headers(), json=body)
    r.raise_for_status()
    return r.json()

def _post(path: str, body: dict) -> dict:
    r = _request("POST", f"{ASC_BASE}{path}", headers=_headers(), json=body)
    if not r.ok:
        raise RuntimeError(f"POST {path} → {r.status_code}: {r.text[:300]}")
    return r.json()

def _delete(path: str) -> None:
    r = _request("DELETE", f"{ASC_BASE}{path}", headers=_headers())
    if not r.ok:
        raise RuntimeError(f"DELETE {path} → {r.status_code}: {r.text[:300]}")

# ---------------------------------------------------------------------------
# App Store Connect resource helpers
# ---------------------------------------------------------------------------

def _get_editable_version(app_id: str, platform: str) -> str:
    """Return the version ID that is currently open for editing."""
    data = _get(f"/apps/{app_id}/appStoreVersions", {
        "filter[platform]": platform,
        "filter[appStoreState]": "PREPARE_FOR_SUBMISSION",
    })
    versions = data.get("data", [])
    if not versions:
        raise RuntimeError(
            f"No PREPARE_FOR_SUBMISSION version for app {app_id} / {platform}. "
            "Create a new version in App Store Connect first."
        )
    return versions[0]["id"]

def _get_version_localizations(version_id: str) -> dict:
    """Return {asc_locale: localization_id} for a version."""
    data = _get(f"/appStoreVersions/{version_id}/appStoreVersionLocalizations")
    return {
        item["attributes"]["locale"]: item["id"]
        for item in data.get("data", [])
    }

def _create_version_localization(version_id: str, asc_locale: str) -> str:
    """Create a version localization for a locale and return its ID.

    A fresh version usually starts with only the primary locale (en-US); the
    others must be created before their fields can be set.
    """
    resp = _post("/appStoreVersionLocalizations", {
        "data": {
            "type": "appStoreVersionLocalizations",
            "attributes": {"locale": asc_locale},
            "relationships": {
                "appStoreVersion": {
                    "data": {"type": "appStoreVersions", "id": version_id}
                }
            },
        }
    })
    return resp["data"]["id"]

# Editable AppInfo states. WAITING_FOR_REVIEW is editable (metadata can be revised
# while in review); only PENDING_DEVELOPER_RELEASE (approved, awaiting release) and
# READY_FOR_SALE (live) truly lock subtitle/privacy.
_EDITABLE_APP_INFO_STATES = (
    "PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED",
    "REJECTED", "METADATA_REJECTED", "READY_FOR_REVIEW",
    "WAITING_FOR_REVIEW", None,
)

def _get_app_info_id(app_id: str) -> str:
    """Return the editable AppInfo ID (name/subtitle/privacy live here, app-wide).

    Raises if none is editable — e.g. an already-approved version in
    PENDING_DEVELOPER_RELEASE locks these app-wide fields until it ships.
    """
    data = _get(f"/apps/{app_id}/appInfos")["data"]
    for item in data:
        if item["attributes"].get("appStoreState") in _EDITABLE_APP_INFO_STATES:
            return item["id"]
    states = ", ".join(f"{i['id']}={i['attributes'].get('appStoreState')}" for i in data)
    raise RuntimeError(
        "no editable AppInfo — subtitle/privacy are locked in the current state.\n"
        f"    AppInfos: {states}\n"
        "    An approved, unreleased version (PENDING_DEVELOPER_RELEASE) freezes these\n"
        "    app-wide fields. Release or reject that version to edit subtitle/privacy."
    )

def _get_info_localizations(app_info_id: str) -> dict:
    """Return {asc_locale: localization_id} for an AppInfo."""
    data = _get(f"/appInfos/{app_info_id}/appInfoLocalizations")
    return {
        item["attributes"]["locale"]: item["id"]
        for item in data.get("data", [])
    }

# ---------------------------------------------------------------------------
# Markdown parser
# ---------------------------------------------------------------------------

def _load_md(locale: str) -> str:
    if locale == "en":
        path = os.path.join(META_DIR, "metadata.md")
    else:
        path = os.path.join(META_DIR, "i18n", f"{locale}.md")
    return open(path, encoding="utf-8").read()

def _extract_section(md: str, heading: str) -> str:
    """Return the text between '## heading' and the next '## ' or end."""
    pattern = rf"(?m)^## {re.escape(heading)}\b.*?(?=^## |\Z)"
    m = re.search(pattern, md, re.DOTALL | re.MULTILINE)
    return m.group(0) if m else ""

def _extract_field(section: str, field_prefix: str) -> str | None:
    """
    Extract the content of the first fenced code block after a
    '### <field_prefix>' heading (prefix match, ignores char counts).
    Any explanatory prose between the heading and the fence is skipped.
    """
    pattern = rf"(?ms)^### {re.escape(field_prefix)}[^\n]*\n.*?^```\n(.*?)\n```"
    m = re.search(pattern, section)
    return m.group(1).strip() if m else None

# ---------------------------------------------------------------------------
# Auth check
# ---------------------------------------------------------------------------

_REQUIRED_VARS = [
    "ASC_KEY_ID",
    "ASC_ISSUER_ID",
    "ASC_PRIVATE_KEY_PATH",
    "ASC_APP_ID",
]

def check_auth() -> None:
    """Validate env vars, key file, and live API token. Run before first push."""
    ok = True

    # 1. Env vars
    print("── Environment variables ──")
    for var in _REQUIRED_VARS:
        val = os.environ.get(var)
        if val:
            display = val if "APP_ID" in var or "KEY_ID" in var else f"{val[:6]}…"
            print(f"  ✓ {var} = {display}")
        else:
            print(f"  ✗ {var} is NOT set")
            ok = False

    if not ok:
        print("\nFix missing env vars in ~/.zshrc, then run: source ~/.zshrc")
        sys.exit(1)

    # 2. Private key file
    print("\n── Private key file ──")
    key_path = os.environ["ASC_PRIVATE_KEY_PATH"]
    if not os.path.exists(key_path):
        print(f"  ✗ File not found: {key_path}")
        print("    Download the .p8 file from App Store Connect and place it there.")
        sys.exit(1)
    key_content = open(key_path).read()
    if "BEGIN EC PRIVATE KEY" not in key_content and "BEGIN PRIVATE KEY" not in key_content:
        print(f"  ✗ {key_path} does not look like a valid .p8 key")
        print("    Expected a PEM file starting with -----BEGIN EC PRIVATE KEY-----")
        sys.exit(1)
    print(f"  ✓ {key_path} exists and looks valid")

    # 3. JWT generation
    print("\n── JWT token ──")
    try:
        token = _make_token()
        print(f"  ✓ Token generated ({token[:24]}…)")
    except Exception as e:
        print(f"  ✗ Failed to generate token: {e}")
        print("    Check that ASC_KEY_ID matches the key used to create the .p8 file.")
        sys.exit(1)

    # 4. Live API call
    print("\n── Live API call ──")
    try:
        data = _get("/apps", {"limit": 10})
        apps = data.get("data", [])
        if not apps:
            print("  ⚠ Auth succeeded but no apps found.")
            print("    The API key may have limited scope — check its role in App Store Connect.")
        else:
            print(f"  ✓ Auth OK — {len(apps)} app(s) visible:")
            for a in apps:
                attr = a["attributes"]
                print(f"      {attr.get('name', '?')}  (id={a['id']}, bundle={attr.get('bundleId', '?')})")
    except requests.HTTPError as e:
        code = e.response.status_code
        body = e.response.text[:300]
        if code == 401:
            print(f"  ✗ 401 Unauthorized — token rejected by ASC.")
            print("    → Check ASC_ISSUER_ID exactly matches the Issuer ID shown in App Store Connect.")
            print("    → Check ASC_KEY_ID matches the Key ID of the downloaded .p8 file.")
            print("    → Revoked keys cannot be re-downloaded — generate a new one if needed.")
        elif code == 403:
            print(f"  ✗ 403 Forbidden — authenticated but insufficient permissions.")
            print("    → The API key needs at least App Manager role.")
        else:
            print(f"  ✗ HTTP {code}: {body}")
        sys.exit(1)

    # 5. App ID spot-check
    print("\n── App ID check ──")
    found_ids = {a["id"] for a in apps}
    app_id = os.environ["ASC_APP_ID"]
    if app_id in found_ids:
        print(f"  ✓ ASC_APP_ID ({app_id}) is visible")
    else:
        print(f"  ⚠ ASC_APP_ID ({app_id}) not in the app list above.")
        print(f"    It may exist on a different team, or the key has limited app access.")

    print("\n✓ Auth check passed. You're ready to push metadata.")


# ---------------------------------------------------------------------------
# Screenshot sync
# ---------------------------------------------------------------------------

def _screenshot_dir(locale: str) -> str:
    """Per-locale screenshot folder (see SCREENSHOT_TEMPLATE)."""
    return SCREENSHOT_TEMPLATE.format(locale=locale)

def _screenshot_files(locale: str, pattern: str) -> list:
    return sorted(glob.glob(os.path.join(_screenshot_dir(locale), pattern)))

def _get_or_create_screenshot_set(loc_id: str, display_type: str) -> str:
    """Return the screenshot set ID for a display type, creating it if absent."""
    sets = _get(f"/appStoreVersionLocalizations/{loc_id}/appScreenshotSets")["data"]
    for s in sets:
        if s["attributes"]["screenshotDisplayType"] == display_type:
            return s["id"]
    resp = _post("/appScreenshotSets", {
        "data": {
            "type": "appScreenshotSets",
            "attributes": {"screenshotDisplayType": display_type},
            "relationships": {
                "appStoreVersionLocalization": {
                    "data": {"type": "appStoreVersionLocalizations", "id": loc_id}
                }
            },
        }
    })
    return resp["data"]["id"]

def _clear_screenshot_set(set_id: str) -> int:
    """Delete every screenshot in a set. Returns how many were removed."""
    shots = _get(f"/appScreenshotSets/{set_id}/appScreenshots")["data"]
    for sh in shots:
        _delete(f"/appScreenshots/{sh['id']}")
    return len(shots)

def _upload_screenshot(set_id: str, path: str) -> str:
    """Reserve, upload, and commit one screenshot. Returns its ID."""
    data     = open(path, "rb").read()
    filename = os.path.basename(path)
    resp = _post("/appScreenshots", {
        "data": {
            "type": "appScreenshots",
            "attributes": {"fileName": filename, "fileSize": len(data)},
            "relationships": {
                "appScreenshotSet": {"data": {"type": "appScreenshotSets", "id": set_id}}
            },
        }
    })
    sid = resp["data"]["id"]
    for op in resp["data"]["attributes"]["uploadOperations"]:
        hdrs  = {h["name"]: h["value"] for h in op["requestHeaders"]}
        chunk = data[op["offset"]: op["offset"] + op["length"]]
        r = _request("PUT", op["url"], headers=hdrs, data=chunk)
        r.raise_for_status()
    _patch(f"/appScreenshots/{sid}", {
        "data": {
            "type": "appScreenshots",
            "id":   sid,
            "attributes": {
                "sourceFileChecksum": hashlib.md5(data).hexdigest(),
                "uploaded": True,
            },
        }
    })
    return sid

def _reorder_screenshot_set(set_id: str, ordered_ids: list) -> None:
    body = {"data": [{"type": "appScreenshots", "id": i} for i in ordered_ids]}
    r = _request(
        "PATCH",
        f"{ASC_BASE}/appScreenshotSets/{set_id}/relationships/appScreenshots",
        headers=_headers(), json=body,
    )
    r.raise_for_status()

def sync_screenshots(platform_key: str, locale_filter: str | None) -> None:
    """Replace screenshots for a platform across all (or one) locales.

    Each locale gets its OWN images (captions are baked into the PNG per language),
    resolved from _screenshot_dir(locale). Idempotent: each run leaves exactly the
    files on disk, in filename order.
    """
    cfg = SCREENSHOT_CFG.get(platform_key, [])
    platform, _ = PLATFORM_CFG[platform_key]
    app_id = os.environ["ASC_APP_ID"]
    locales = [locale_filter] if locale_filter else list(LOCALE_MAP.keys())

    print(f"\n=== Screenshots: {platform_key.upper()} ===")

    version_id   = _get_editable_version(app_id, platform)
    version_locs = _get_version_localizations(version_id)

    for locale in locales:
        asc_locale = LOCALE_MAP[locale]
        loc_id = version_locs.get(asc_locale)
        if not loc_id:
            print(f"  [{asc_locale}] no version localization — skip")
            continue
        src = os.path.basename(_screenshot_dir(locale).rstrip("/"))
        for display_type, pattern in cfg:
            files = _screenshot_files(locale, pattern)
            if not files:
                print(f"  [{asc_locale}] {display_type}: no files match {pattern} in {src}/ — skip")
                continue
            set_id  = _get_or_create_screenshot_set(loc_id, display_type)
            removed = _clear_screenshot_set(set_id)
            ids     = [_upload_screenshot(set_id, f) for f in files]
            _reorder_screenshot_set(set_id, ids)
            print(f"  [{asc_locale}] {display_type}: replaced {removed} → uploaded {len(ids)} from {src}/")


# ---------------------------------------------------------------------------
# App-preview (video) sync — mirrors the screenshot reserve→upload→commit flow,
# but with appPreviewSets/appPreviews. Videos are locale-agnostic (no captions),
# so the same file is pushed to every locale.
# ---------------------------------------------------------------------------

def _get_or_create_preview_set(loc_id: str, preview_type: str) -> str:
    sets = _get(f"/appStoreVersionLocalizations/{loc_id}/appPreviewSets")["data"]
    for s in sets:
        if s["attributes"]["previewType"] == preview_type:
            return s["id"]
    resp = _post("/appPreviewSets", {
        "data": {
            "type": "appPreviewSets",
            "attributes": {"previewType": preview_type},
            "relationships": {
                "appStoreVersionLocalization": {
                    "data": {"type": "appStoreVersionLocalizations", "id": loc_id}
                }
            },
        }
    })
    return resp["data"]["id"]

def _clear_preview_set(set_id: str) -> int:
    prevs = _get(f"/appPreviewSets/{set_id}/appPreviews")["data"]
    for p in prevs:
        _delete(f"/appPreviews/{p['id']}")
    return len(prevs)

def _upload_preview(set_id: str, path: str) -> str:
    """Reserve, chunk-upload, and commit one app preview video. Returns its ID.
    Apple then processes the video asynchronously (PROCESSING → COMPLETE/FAILED)."""
    data     = open(path, "rb").read()
    filename = os.path.basename(path)
    resp = _post("/appPreviews", {
        "data": {
            "type": "appPreviews",
            "attributes": {"fileName": filename, "fileSize": len(data)},
            "relationships": {
                "appPreviewSet": {"data": {"type": "appPreviewSets", "id": set_id}}
            },
        }
    })
    pid = resp["data"]["id"]
    for op in resp["data"]["attributes"]["uploadOperations"]:
        hdrs  = {h["name"]: h["value"] for h in op["requestHeaders"]}
        chunk = data[op["offset"]: op["offset"] + op["length"]]
        r = _request("PUT", op["url"], headers=hdrs, data=chunk)
        r.raise_for_status()
    _patch(f"/appPreviews/{pid}", {
        "data": {
            "type": "appPreviews",
            "id":   pid,
            "attributes": {
                "sourceFileChecksum": hashlib.md5(data).hexdigest(),
                "uploaded": True,
            },
        }
    })
    return pid

def sync_previews(platform_key: str, locale_filter: str | None) -> None:
    """Replace app-preview videos for a platform across all (or one) locale(s).
    The SAME captionless file is uploaded to every locale."""
    cfg = PREVIEW_CFG.get(platform_key, [])
    if not cfg:
        print(f"  (no preview config for {platform_key}) — skip")
        return
    platform, _ = PLATFORM_CFG[platform_key]
    app_id = os.environ["ASC_APP_ID"]
    locales = [locale_filter] if locale_filter else list(LOCALE_MAP.keys())

    print(f"\n=== App previews: {platform_key.upper()} ===")
    version_id   = _get_editable_version(app_id, platform)
    version_locs = _get_version_localizations(version_id)

    for locale in locales:
        asc_locale = LOCALE_MAP[locale]
        loc_id = version_locs.get(asc_locale)
        if not loc_id:
            print(f"  [{asc_locale}] no version localization — skip")
            continue
        for preview_type, filename in cfg:
            path = os.path.join(VIDEO_DIR, filename)
            if not os.path.exists(path):
                print(f"  [{asc_locale}] {preview_type}: missing {filename} — skip")
                continue
            set_id  = _get_or_create_preview_set(loc_id, preview_type)
            removed = _clear_preview_set(set_id)
            pid     = _upload_preview(set_id, path)
            print(f"  [{asc_locale}] {preview_type}: replaced {removed} → uploaded {filename}")


# ---------------------------------------------------------------------------
# Push logic
# ---------------------------------------------------------------------------

_GLOBAL_URLS = None

def _global_urls() -> dict:
    """URLs that are the same for every locale (supportUrl), from the en '## Common'."""
    global _GLOBAL_URLS
    if _GLOBAL_URLS is None:
        common = _extract_section(_load_md("en"), "Common")
        _GLOBAL_URLS = {}
        for prefix, field in GLOBAL_URL_FIELDS.items():
            val = _extract_field(common, prefix)
            if val:
                _GLOBAL_URLS[field] = val
    return _GLOBAL_URLS

def push_platform(platform_key: str, locale_filter: str | None) -> None:
    platform, section_heading = PLATFORM_CFG[platform_key]
    app_id = os.environ["ASC_APP_ID"]
    locales = [locale_filter] if locale_filter else list(LOCALE_MAP.keys())

    print(f"\n=== {platform_key.upper()} (app {app_id}) ===")

    version_id   = _get_editable_version(app_id, platform)
    version_locs = _get_version_localizations(version_id)

    for locale in locales:
        asc_locale = LOCALE_MAP[locale]
        md = _load_md(locale)
        section = _extract_section(md, section_heading)
        if not section:
            print(f"  [{locale}] no '{section_heading}' section found — skipping")
            continue

        print(f"  [{locale} → {asc_locale}]")

        # --- AppStoreVersionLocalization (description, keywords, promo, whatsNew) ---
        # Create the localization if the version doesn't have it yet (a fresh
        # version starts with only the primary locale).
        ver_loc_id = version_locs.get(asc_locale)
        if not ver_loc_id:
            ver_loc_id = _create_version_localization(version_id, asc_locale)
            version_locs[asc_locale] = ver_loc_id
            print(f"    + created version localization")

        ver_attrs = {}
        for md_prefix, api_field in VERSION_LOC_FIELDS.items():
            value = _extract_field(section, md_prefix)
            if value is not None:
                ver_attrs[api_field] = value
        # Localized marketing URL from THIS locale's '## Common'; shared support URL from en.
        loc_common = _extract_section(md, "Common")
        for md_prefix, api_field in LOCALIZED_URL_FIELDS.items():
            value = _extract_field(loc_common, md_prefix)
            if value is not None:
                ver_attrs[api_field] = value
        ver_attrs.update(_global_urls())
        if ver_attrs:
            _patch(f"/appStoreVersionLocalizations/{ver_loc_id}", {
                "data": {
                    "type": "appStoreVersionLocalizations",
                    "id": ver_loc_id,
                    "attributes": ver_attrs,
                }
            })
            print(f"    ✓ version loc: {', '.join(ver_attrs)}")


def push_app_info(locale_filter: str | None) -> None:
    """Push shared subtitle + privacy-policy text from each file's Common section.

    These live in the app-wide AppInfo (one per app, not per platform), so this
    runs once — not per platform — and reads from '## Common', not a platform section.
    """
    app_id = os.environ["ASC_APP_ID"]
    locales = [locale_filter] if locale_filter else list(LOCALE_MAP.keys())

    print(f"\n=== Shared app info (subtitle, privacy) ===")

    try:
        app_info_id = _get_app_info_id(app_id)
    except RuntimeError as e:
        print(f"  ⚠ {e}")
        return
    info_locs = _get_info_localizations(app_info_id)

    for locale in locales:
        asc_locale = LOCALE_MAP[locale]
        section    = _extract_section(_load_md(locale), "Common")
        if not section:
            print(f"  [{locale}] no '## Common' section — skipping")
            continue

        info_loc_id = info_locs.get(asc_locale)
        if not info_loc_id:
            print(f"  [{asc_locale}] no app-info localization — skipping")
            continue

        info_attrs = {}
        for md_prefix, api_field in INFO_LOC_FIELDS.items():
            value = _extract_field(section, md_prefix)
            if value is not None:
                info_attrs[api_field] = value
        if info_attrs:
            _patch(f"/appInfoLocalizations/{info_loc_id}", {
                "data": {
                    "type": "appInfoLocalizations",
                    "id": info_loc_id,
                    "attributes": info_attrs,
                }
            })
            print(f"  [{asc_locale}] ✓ {', '.join(info_attrs)}")
        else:
            print(f"  [{asc_locale}] ⚠ no subtitle/privacy found in Common section")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    if args[0] == "--check":
        check_auth()
        return

    # `screenshots <platform> [locale]` — replace screenshots only.
    # `appinfo [locale]`                 — push shared subtitle + privacy only.
    # `<platform> [locale]`              — push platform text + shared app info.
    # `<platform> [locale] --screenshots` / `--with-screenshots` — also screenshots.
    mode = "text"
    if args[0] == "screenshots":
        mode = "screenshots"
        args = args[1:]
        if not args:
            print("Usage: screenshots <platform|all> [locale]")
            sys.exit(1)
    elif args[0] == "previews":
        mode = "previews"
        args = args[1:]
        if not args:
            print("Usage: previews <platform|all> [locale]")
            sys.exit(1)
    elif args[0] == "appinfo":
        locale_arg = args[1] if len(args) > 1 else None
        if locale_arg and locale_arg not in LOCALE_MAP:
            print(f"Unknown locale '{locale_arg}'. Known: {', '.join(LOCALE_MAP)}")
            sys.exit(1)
        push_app_info(locale_arg)
        print("\nDone.")
        return

    with_screenshots = with_previews = False
    kept = []
    for a in args:
        if a in ("--screenshots", "--with-screenshots"):
            with_screenshots = True
        elif a in ("--previews", "--with-previews"):
            with_previews = True
        else:
            kept.append(a)
    args = kept

    platform_arg = args[0].lower()
    locale_arg   = args[1] if len(args) > 1 else None

    if locale_arg and locale_arg not in LOCALE_MAP:
        print(f"Unknown locale '{locale_arg}'. Known: {', '.join(LOCALE_MAP)}")
        sys.exit(1)

    platforms = list(PLATFORM_CFG.keys()) if platform_arg == "all" else [platform_arg]
    for p in platforms:
        if p not in PLATFORM_CFG:
            print(f"Unknown platform '{p}'. Use: ios, macos, tvos, all")
            sys.exit(1)
        if mode == "screenshots":
            sync_screenshots(p, locale_arg)
        elif mode == "previews":
            sync_previews(p, locale_arg)
        else:
            push_platform(p, locale_arg)
            if with_screenshots:
                sync_screenshots(p, locale_arg)
            if with_previews:
                sync_previews(p, locale_arg)

    # Subtitle + privacy are app-wide, so push them once after any text push.
    if mode == "text":
        push_app_info(locale_arg)

    print("\nDone.")

if __name__ == "__main__":
    main()
