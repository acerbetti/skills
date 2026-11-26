#!/usr/bin/env python3
"""
push-amazon.py — Push Amazon Appstore listing metadata + images from local files.

Amazon (like Google Play) uses an EDIT model: create an edit → PUT localized
listings / upload images → commit. BUT there is a crucial difference: Amazon has
NO "validate without committing" and NO Managed-Publishing staging gate —
**committing submits the edit for review and publishing immediately**. So there
are three modes:

  * default is DRY-RUN and does a purely LOCAL preview (no server writes at all);
  * --stage opens a real edit, applies changes, and LEAVES IT OPEN (uncommitted).
    That open edit IS the "upcoming version" — review and Submit it in the Console.
    This is the recommended human gate (the equivalent of Play's Managed Publishing).
  * --commit opens an edit, applies everything, and COMMITS = submits for review now.

Usage:
  python3 push-amazon.py --check                          # verify creds + app access (read-only)
  python3 push-amazon.py listing [locale] [--stage|--commit]            # text + screenshots
  python3 push-amazon.py all [locale] [--stage|--commit]                # text + screenshots + artwork + video
  python3 push-amazon.py text [locale] [--stage|--commit]               # title/desc/features/keywords/notes
  python3 push-amazon.py screenshots [locale] [--stage|--commit]        # Fire TV + Fire Tablet images (replace)
  python3 push-amazon.py video [locale] [--stage|--commit]              # promo video → every locale (captionless)
  python3 push-amazon.py assets [--stage|--commit]                      # icons + Fire TV artwork

  Add --amend to --stage to use the EXISTING open edit rather than creating a new one.
  Useful for adding assets to an already-staged upcoming version:
    python3 push-amazon.py screenshots --stage --amend   # add shots to existing open edit
    python3 push-amazon.py video --stage --amend         # add video to existing open edit
    python3 push-amazon.py all --stage --amend           # top up everything in existing edit

  --amend can also be combined with --commit to submit an already-open edit for review:
    python3 push-amazon.py video --commit --amend        # add video then submit immediately

Scope: store listing only. The APK/AAB is owned by CI (Fire TV publish job).

Required env vars:
  AMAZON_CLIENT_ID       LWA security-profile client id     (not secret)
  AMAZON_CLIENT_SECRET   LWA security-profile client secret (SECRET — keep out of the repo)
  AMAZON_APP_ID          the Amazon app id (amzn1.devportal.mobileapp...)

Deps: none — pure stdlib (the Amazon API is plain REST + a bearer token), so unlike
the google-play / app-store-connect skills this needs no venv.

NOTE: auth, the edit lifecycle, listing-text PUTs (incl. the array fields), image
upload/replace, asset replace, and every image-type slug were all verified live via
throwaway edits. Only the final commit path (POST /edits/{id}/commit) is unexercised,
since committing submits for review. Prefer --stage.
"""

import os, re, sys, glob, json, time, urllib.parse, urllib.request, urllib.error

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SCOPE     = "appstore::apps:readwrite"

def _app_id() -> str:
    aid = os.environ.get("AMAZON_APP_ID")
    if not aid:
        sys.exit("✗ AMAZON_APP_ID is not set")
    return aid

def _base() -> str:
    return f"https://developer.amazon.com/api/appstore/v1/applications/{_app_id()}"

META_DIR = os.environ.get(
    "AMAZON_META_DIR",
    os.path.join(os.getcwd(), "store-listings", "amazon-appstore"),
)
# Screenshots come from the Android pipeline output (app-source artwork), per locale.
SCREENSHOT_TEMPLATE = os.environ.get(
    "AMAZON_SCREENSHOT_TEMPLATE",
    os.path.join(os.getcwd(), "android-native", "screenshots", "captioned", "{sdir}"),
)

# ---------------------------------------------------------------------------
# Locales
# ---------------------------------------------------------------------------
#
# The locale set is DISCOVERED from the project's own files, not hardcoded, so
# adding a language needs no change here. See _discover_locales() below.
#
# Three naming schemes meet here:
#   * Amazon listing language — region-qualified: en-US, it-IT, ja-JP
#   * metadata file name      — the skill's local code: en, it, ja
#   * screenshot folder       — the skill's local code (same as the file name)
#
# _AMAZON_LOCALE_ALIASES holds the cases where Amazon's language differs from
# the local code. Everything else is derived from the local code by
# _amazon_locale_for(), which asks Amazon's own listing endpoint when
# credentials are available and falls back to the region defaults otherwise.
#
# Note: Amazon's supported languages differ per app and can change. A language
# Amazon rejects comes back as 400 "Invalid language" — that is the authority,
# not this table.

# local code → Amazon listing language, where they differ.
_AMAZON_LOCALE_ALIASES = {
    "en": "en-US",
    "it": "it-IT",
    "ja": "ja-JP",
    "zh-Hans": "zh-CN",
    "zh-Hant": "zh-TW",
}

# Fallback when nothing can be discovered from disk.
LOCALES = [
    ("en-US", "metadata.md",   "en"),
    ("de-DE", "i18n/de.md",    "de"),
    ("fr-FR", "i18n/fr.md",    "fr"),
    ("es-ES", "i18n/es-ES.md", "es-ES"),
    ("it-IT", "i18n/it.md",    "it"),
    ("ja-JP", "i18n/ja.md",    "ja"),
]

# local code → Amazon language, used when Amazon's own list isn't reachable.
_AMAZON_REGION_DEFAULTS = {
    "en": "en-US", "de": "de-DE", "fr": "fr-FR", "es": "es-ES", "it": "it-IT",
    "ja": "ja-JP", "ko": "ko-KR", "pt": "pt-BR", "ru": "ru-RU", "zh": "zh-CN",
    "nl": "nl-NL", "pl": "pl-PL", "tr": "tr-TR", "da": "da-DK", "sv": "sv-SE",
    "no": "no-NO", "fi": "fi-FI", "el": "el-GR", "hu": "hu-HU", "ro": "ro-RO",
    "uk": "uk-UA", "vi": "vi-VN", "th": "th-TH", "id": "id-ID", "cs": "cs-CZ",
    "sk": "sk-SK", "he": "he-IL", "hi": "hi-IN", "ar": "ar", "ca": "ca-ES",
}

# Cached Amazon listing-language index: local code → Amazon language.
_AMAZON_LOCALE_INDEX = None

# The languages probed against the app when an edit is open. Amazon's available
# set is per-app, so this is a probe list, not an allowlist — anything the app
# accepts is usable.
_CANDIDATE_LANGUAGES = [
    "en-US", "de-DE", "fr-FR", "es-ES", "it-IT", "ja-JP", "ko-KR", "pt-BR",
    "nl-NL", "pl-PL", "ru-RU", "tr-TR", "da-DK", "sv-SE", "no-NO", "fi-FI",
    "el-GR", "hu-HU", "ro-RO", "uk-UA", "vi-VN", "th-TH", "id-ID", "cs-CZ",
    "sk-SK", "he-IL", "hi-IN", "ar", "ca-ES", "zh-CN", "zh-TW",
]


def _amazon_locale_index() -> dict:
    """Probe which listing languages the app accepts, and index them by local code.

    Amazon's API has no "list all languages" endpoint — a listing is fetched per
    language (`/edits/{id}/listings/{lang}`) and a language the app isn't offered
    comes back as 400 "Invalid language". So the index is built by probing the
    candidate languages.

    Probing needs an open edit, and opening one is a server write, so this only
    probes when an edit already exists. Otherwise the static aliases and region
    defaults decide, and Amazon's own 400 "Invalid language" is the authority
    when a language is pushed.

    Failures are non-fatal: the static aliases and region defaults still apply.
    """
    global _AMAZON_LOCALE_INDEX
    if _AMAZON_LOCALE_INDEX is not None:
        return _AMAZON_LOCALE_INDEX
    index = {}
    try:
        eid, _ = _active_edit()
        if eid:
            for lang in _CANDIDATE_LANGUAGES:
                try:
                    _api("GET", f"/edits/{eid}/listings/{lang}")
                except SystemExit:
                    continue  # 400 Invalid language — this app isn't offered it
                index[lang] = lang
                index.setdefault(lang.split("-")[0], lang)
    except Exception:
        pass
    _AMAZON_LOCALE_INDEX = index
    return index


def _amazon_locale_for(local: str) -> str:
    """Map a local code to the Amazon listing language for it."""
    if local in _AMAZON_LOCALE_ALIASES:
        return _AMAZON_LOCALE_ALIASES[local]
    index = _amazon_locale_index()
    if local in index:
        return index[local]
    if local in _AMAZON_REGION_DEFAULTS:
        return _AMAZON_REGION_DEFAULTS[local]
    return local


def _discover_locales() -> list:
    """Build the (lang, md_rel, sdir) table from what the project has.

    Sources, in order of confidence:
      1. i18n/<local>.md files in META_DIR
      2. metadata.md (the primary locale)
      3. screenshot folders under SCREENSHOT_TEMPLATE's directory

    The local code is the file name without .md; the primary locale's file is
    metadata.md, so its local code is "en".
    """
    found = set()

    meta_dir = os.path.abspath(META_DIR)
    if os.path.isdir(meta_dir):
        if os.path.isfile(os.path.join(meta_dir, "metadata.md")):
            found.add("en")
        i18n_dir = os.path.join(meta_dir, "i18n")
        if os.path.isdir(i18n_dir):
            for name in os.listdir(i18n_dir):
                if name.endswith(".md"):
                    found.add(name[:-3])

    try:
        shots_root = os.path.abspath(SCREENSHOT_TEMPLATE).split("{sdir}")[0]
    except Exception:
        shots_root = ""
    if shots_root and os.path.isdir(shots_root):
        for name in os.listdir(shots_root):
            if os.path.isdir(os.path.join(shots_root, name)):
                found.add(name)

    if not found:
        return list(LOCALES)

    table = []
    for local in sorted(found):
        md_rel = "metadata.md" if local == "en" else f"i18n/{local}.md"
        table.append((_amazon_locale_for(local), md_rel, local))
    return table


def _refresh_locales() -> list:
    """Refresh the locale table from disk. Call once at startup, before any push."""
    global LOCALES
    LOCALES = _discover_locales()
    return LOCALES


def _resolve_locale_arg(locale_arg):
    """Accept a local code (it, zh-Hans) or an Amazon language (it-IT, zh-CN)
    and return the AMAZON language, or None if unknown."""
    if not locale_arg:
        return None
    known = {lang for lang, _, _ in LOCALES}
    if locale_arg in known:
        return locale_arg
    # local code → Amazon language (it → it-IT, zh-Hans → zh-CN)
    for lang, _, sdir in LOCALES:
        if sdir == locale_arg:
            return lang
    return False

# MD heading prefix → Amazon listing field. Amazon supports two fields Play does not:
# featureBullets (array) and keywords (array). recentChanges is the per-language
# release-notes field (Amazon keeps it on the listing, not on a separate track).
STR_FIELDS = [
    ("Title",             "title"),
    ("Short description", "shortDescription"),
    ("Long description",  "fullDescription"),
    ("Release notes",     "recentChanges"),
]
# arrays: parsed from a fenced block into a list
BULLET_FIELD  = ("Product features", "featureBullets")   # one bullet per line
KEYWORD_FIELD = ("Search keywords",  "keywords")         # comma-separated

# Android capture prefix → Amazon image type (multi-image sets; replace = delete+upload).
# The app's Amazon story is Fire TV, so by default we push ONLY Fire TV screenshots.
# The general `screenshots` slot is what Fire *Tablets* show (there is no separate
# tablet slug); the dead Fire Phone is irrelevant and Amazon-Appstore-on-Android-phone
# is a marginal audience. To add Fire Tablet coverage, enable the tablet line below —
# but curate it first: tablet_06-cast-* shows Google Cast, which this build lacks.
SCREENSHOT_TYPES = [
    ("firetv-screenshots", "tv_*.png"),      # Fire TV — 1920×1080
    ("screenshots",        "tablet_*.png"),  # Fire Tablet (shared general slot); Cast shot auto-excluded
]

# Single-image listing artwork (replace-on-upload), pushed to the default language.
# Slugs verified live (GET returns 200 + the currently-populated slot for each).
ASSETS = [
    ("large-icons",        "assets/icon-512.png"),
    ("small-icons",        "assets/icon-114.png"),
    ("firetv-backgrounds", "assets/fire-tv-background-1920x1080.png"),
    ("firetv-icons",       "assets/fire-tv-banner-1280x720.png"),  # the 1280×720 Fire TV store tile
]
DEFAULT_LANG = "en-US"

# Promo video → the `videos` asset set. The video has NO captions/text overlays, so
# ONE locale-agnostic file is uploaded to every locale (not per-locale like screenshots).
VIDEO_DIR = os.environ.get(
    "AMAZON_VIDEO_DIR",
    os.path.join(os.getcwd(), "store-listings", "assets", "video"),
)
VIDEO_FILE = os.environ.get("AMAZON_VIDEO_FILE", "clocks-in-motion-amazon-appstore-draft.mp4")

_RETRY_STATUS = {429, 500, 502, 503, 504}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_TOKEN = None

def _token(force: bool = False) -> str:
    global _TOKEN
    if _TOKEN and not force:
        return _TOKEN
    cid = os.environ.get("AMAZON_CLIENT_ID")
    sec = os.environ.get("AMAZON_CLIENT_SECRET")
    if not cid or not sec:
        sys.exit("✗ AMAZON_CLIENT_ID / AMAZON_CLIENT_SECRET not set")
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": cid,
        "client_secret": sec,
        "scope": SCOPE,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            _TOKEN = json.loads(r.read())["access_token"]
    except urllib.error.HTTPError as e:
        sys.exit(f"✗ token request failed: {e.code} {e.reason}\n{e.read().decode(errors='replace')}")
    return _TOKEN

# ---------------------------------------------------------------------------
# REST helper: returns (status, headers, parsed_body). Retries transient 5xx/429,
# refreshes the token once on 403, and threads the ETag via If-Match when given.
# ---------------------------------------------------------------------------

def _api(method: str, path: str, body=None, etag: str = None,
         raw: bytes = None, content_type: str = None):
    url = _base() + path
    for attempt in range(5):
        headers = {"Authorization": f"Bearer {_token()}", "Accept": "application/json"}
        if etag:
            headers["If-Match"] = etag
        if raw is not None:
            payload = raw
            headers["Content-Type"] = content_type or "application/octet-stream"
        elif body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        else:
            payload = None
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read()
                parsed = json.loads(text) if text and r.headers.get_content_type() == "application/json" else None
                return r.status, dict(r.headers), parsed
        except urllib.error.HTTPError as e:
            if e.code == 403 and attempt == 0:      # token likely expired
                _token(force=True)
                continue
            if e.code in _RETRY_STATUS and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            detail = e.read().decode(errors="replace")
            raise SystemExit(f"✗ {method} {path} → {e.code} {e.reason}\n{detail}")
        except urllib.error.URLError as e:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise SystemExit(f"✗ {method} {path} → network error: {e}")

# ---------------------------------------------------------------------------
# Markdown parsing (### heading + first fenced block; heading may carry a suffix)
# ---------------------------------------------------------------------------

def _load_md(rel_path: str) -> str:
    return open(os.path.join(META_DIR, rel_path), encoding="utf-8").read()

def _block(text: str, prefix: str):
    # heading may be h2 or h3 (metadata.md keeps Release notes at ##, i18n at ###)
    m = re.search(rf"(?ms)^#{{2,3}} {re.escape(prefix)}[^\n]*\n.*?^```\n(.*?)\n```", text)
    return m.group(1).strip() if m else None

def listing_fields(md_rel: str) -> dict:
    """Build the Amazon listing object (minus language) from one metadata file."""
    text = _load_md(md_rel)
    out = {}
    for prefix, field in STR_FIELDS:
        val = _block(text, prefix)
        if val is not None:
            out[field] = val
    bullets = _block(text, BULLET_FIELD[0])
    if bullets:
        out[BULLET_FIELD[1]] = [
            re.sub(r"^[•\-\*]\s*", "", ln).strip()
            for ln in bullets.splitlines() if ln.strip()
        ]
    kw = _block(text, KEYWORD_FIELD[0])
    if kw:
        # Amazon's `keywords` field is REQUIRED and each entry must be a SINGLE WORD
        # (multi-word phrases → 400 SINGLE_WORD). Our metadata stores comma-separated
        # *phrases* (for Apple/Play), so split them into words here: split on
        # whitespace and hyphens, dedupe case-insensitively, preserve priority order.
        words, seen = [], set()
        for phrase in kw.split(","):
            for w in re.split(r"[\s\-]+", phrase.strip()):
                w = w.strip()
                if w and w.lower() not in seen:
                    seen.add(w.lower())
                    words.append(w)
        out[KEYWORD_FIELD[1]] = words
    return out

def _screenshot_files(sdir: str, pattern: str) -> list:
    d = SCREENSHOT_TEMPLATE.format(sdir=sdir)
    files = sorted(glob.glob(os.path.join(d, pattern)))
    # The Amazon build has NO Google Cast, so a Cast capture (e.g. *_06-cast-*) must
    # never reach Amazon — exclude it from every image set (phone/tablet/anything).
    return [f for f in files if "cast" not in os.path.basename(f).lower()]

# ---------------------------------------------------------------------------
# Edit lifecycle
# ---------------------------------------------------------------------------

def _active_edit():
    """Return (editId, etag) of the current active edit, or (None, None)."""
    try:
        _, headers, body = _api("GET", "/edits")
    except SystemExit as e:
        # 404 means no active edit — treat as (None, None) rather than crashing.
        if "404" in str(e):
            return None, None
        raise
    if body and body.get("id"):
        return body["id"], headers.get("ETag")
    return None, None

def _open_edit(amend: bool = False) -> str:
    eid, _ = _active_edit()
    if eid:
        if amend:
            print(f"  ↩ amending existing open edit {eid}")
            return eid
        raise SystemExit(
            f"✗ an active edit already exists ({eid}). Amazon allows only one open edit.\n"
            "  Commit or discard it in the Developer Console first, then re-run.\n"
            "  Or pass --amend with --stage to add to the existing edit without submitting."
        )
    if amend:
        raise SystemExit("✗ --amend requires an existing open edit, but none found.")
    _, _, body = _api("POST", "/edits")
    return body["id"]

def _commit_edit(edit_id: str):
    # commit needs the current edit ETag
    _, headers, _ = _api("GET", "/edits")
    _api("POST", f"/edits/{edit_id}/commit", etag=headers.get("ETag"))

def _discard_edit(edit_id: str):
    try:
        _, headers, _ = _api("GET", "/edits")
        _api("DELETE", f"/edits/{edit_id}", etag=headers.get("ETag"))
    except SystemExit:
        pass  # best-effort cleanup

# ---------------------------------------------------------------------------
# Push operations (only run under --commit; dry-run previews locally instead)
# ---------------------------------------------------------------------------

def push_text(edit_id, only_locale):
    print("── Listing text ──")
    for lang, md_rel, _ in LOCALES:
        if only_locale and lang != only_locale:
            continue
        fields = listing_fields(md_rel)
        if not fields:
            print(f"  [{lang}] no fields found — skip")
            continue
        # Amazon has no PATCH: PUT replaces the whole listing, so merge onto current.
        _, headers, current = _api("GET", f"/edits/{edit_id}/listings/{lang}")
        body = dict(current or {})
        body["language"] = lang
        body.update(fields)
        _api("PUT", f"/edits/{edit_id}/listings/{lang}", body=body, etag=headers.get("ETag"))
        print(f"  [{lang}] ✓ {', '.join(fields)}")

def _images_state(edit_id, lang, image_type):
    """Return (etag, [image dicts]) for a listing image set (GET body is {'images': [...]})."""
    _, headers, body = _api("GET", f"/edits/{edit_id}/listings/{lang}/{image_type}")
    return headers.get("ETag"), (body or {}).get("images", []) or []

def _replace_images(edit_id, lang, image_type, files):
    # Multi-image set with append semantics: delete existing, then upload the new ones.
    # Every mutation needs the current If-Match ETag, and the ETag changes after each
    # write — including writes to OTHER image types on the same listing. Re-fetch
    # immediately before each operation to avoid 412 Precondition Failed.
    base = f"/edits/{edit_id}/listings/{lang}/{image_type}"
    etag, existing = _images_state(edit_id, lang, image_type)
    for img in existing:
        img_id = img.get("id") if isinstance(img, dict) else img
        if img_id:
            _api("DELETE", f"{base}/{img_id}", etag=etag)
            etag, _ = _images_state(edit_id, lang, image_type)
    for f in files:
        etag, _ = _images_state(edit_id, lang, image_type)  # always re-fetch before upload
        with open(f, "rb") as fh:
            _api("POST", f"{base}/upload", raw=fh.read(), content_type="image/png", etag=etag)

def push_screenshots(edit_id, only_locale):
    print("── Screenshots (replace) ──")
    for lang, _, sdir in LOCALES:
        if only_locale and lang != only_locale:
            continue
        for image_type, pattern in SCREENSHOT_TYPES:
            files = _screenshot_files(sdir, pattern)
            if not files:
                continue
            _replace_images(edit_id, lang, image_type, files)
            print(f"  [{lang}] {image_type}: {len(files)} from {sdir}/")

def push_assets(edit_id):
    # Amazon requires icons and Fire TV artwork for EVERY locale listing, not just the
    # default language. The same files are uploaded to each locale (they are language-agnostic).
    print("── Listing artwork (all locales) ──")
    for lang, _, _ in LOCALES:
        for image_type, rel in ASSETS:
            path = os.path.join(META_DIR, rel)
            if not os.path.exists(path):
                continue
            _replace_images(edit_id, lang, image_type, [path])
            print(f"  [{lang}] ✓ {image_type} ← {os.path.basename(rel)}")

def _videos_state(edit_id, lang):
    """Return (etag, [video dicts]) for a listing's video set (GET body is {'videos': [...]})."""
    _, headers, body = _api("GET", f"/edits/{edit_id}/listings/{lang}/videos")
    return headers.get("ETag"), (body or {}).get("videos", []) or []

def push_video(edit_id, only_locale):
    print("── Promo video (replace) ──")
    path = os.path.join(VIDEO_DIR, VIDEO_FILE)
    if not os.path.exists(path):
        print(f"  missing {VIDEO_FILE} in {VIDEO_DIR} — skip")
        return
    data = open(path, "rb").read()
    for lang, _, _ in LOCALES:               # same captionless file for every locale
        if only_locale and lang != only_locale:
            continue
        base = f"/edits/{edit_id}/listings/{lang}/videos"
        etag, existing = _videos_state(edit_id, lang)
        for v in existing:
            vid = v.get("id") if isinstance(v, dict) else v
            if vid:
                _api("DELETE", f"{base}/{vid}", etag=etag)
                etag, _ = _videos_state(edit_id, lang)
        _api("POST", f"{base}/upload", raw=data, content_type="video/mp4", etag=etag)
        print(f"  [{lang}] ✓ {VIDEO_FILE}")

# ---------------------------------------------------------------------------
# Dry-run preview (no server writes)
# ---------------------------------------------------------------------------

def preview(cmd, only_locale):
    print("(dry-run — local preview only; nothing sent to Amazon)\n")
    if cmd in ("text", "listing", "all"):
        print("── Listing text ──")
        for lang, md_rel, _ in LOCALES:
            if only_locale and lang != only_locale:
                continue
            f = listing_fields(md_rel)
            title = f.get("title", "?")
            extra = []
            if f.get("featureBullets"): extra.append(f"{len(f['featureBullets'])} bullets")
            if f.get("keywords"):       extra.append(f"{len(f['keywords'])} keywords")
            if f.get("recentChanges"):  extra.append("recentChanges")
            print(f"  [{lang}] {title!r} · short {len(f.get('shortDescription',''))}b · "
                  f"full {len(f.get('fullDescription',''))}c"
                  + (f" · {', '.join(extra)}" if extra else ""))
    if cmd in ("screenshots", "listing", "all"):
        print("── Screenshots ──")
        for lang, _, sdir in LOCALES:
            if only_locale and lang != only_locale:
                continue
            for image_type, pattern in SCREENSHOT_TYPES:
                n = len(_screenshot_files(sdir, pattern))
                if n:
                    print(f"  [{lang}] {image_type}: {n} from {sdir}/")
    if cmd in ("assets", "all") and not only_locale:
        print("── Artwork (all locales) ──")
        for image_type, rel in ASSETS:
            ok = "✓" if os.path.exists(os.path.join(META_DIR, rel)) else "missing"
            print(f"  {image_type} ← {rel} → {len(LOCALES)} locales  [{ok}]")
    if cmd in ("video", "all"):
        print("── Promo video ──")
        n = len([l for l, _, _ in LOCALES if not only_locale or l == only_locale])
        ok = "✓" if os.path.exists(os.path.join(VIDEO_DIR, VIDEO_FILE)) else "missing"
        print(f"  videos ← {VIDEO_FILE} → {n} locale(s)  [{ok}]")
    print("\n✓ Dry-run OK. Use --stage to create an upcoming version (open edit, "
          "review in Console), or --commit to submit for review.")

# ---------------------------------------------------------------------------
# Check (read-only)
# ---------------------------------------------------------------------------

def check():
    print("── Credentials ──")
    for v in ("AMAZON_CLIENT_ID", "AMAZON_CLIENT_SECRET", "AMAZON_APP_ID"):
        print(f"  {'✓' if os.environ.get(v) else '✗'} {v}")
    _token()
    print("  ✓ LWA token obtained")
    print("\n── App access ──")
    eid, _ = _active_edit()
    print(f"  ✓ reached app {_app_id()}")
    print(f"    active edit: {eid or '(none)'}")
    _refresh_locales()
    print("\n── Locales discovered ──")
    print(f"  ✓ {len(LOCALES)} locale(s): {', '.join(f'{sdir}→{lang}' for lang, _, sdir in LOCALES)}")
    missing_md = [sdir for _, md_rel, sdir in LOCALES
                  if not os.path.isfile(os.path.join(META_DIR, md_rel))]
    if missing_md:
        print(f"  ⚠ no metadata file for: {', '.join(missing_md)}")
    else:
        print("  ✓ every locale has a metadata file")
    for image_type, pattern in SCREENSHOT_TYPES:
        n = sum(len(_screenshot_files(sdir, pattern)) for _, _, sdir in LOCALES)
        print(f"  · {image_type}: {n} file(s) match '{pattern}' across all locales")
    print("\n✓ Auth + app access OK. Dry-run by default; --commit submits for review.")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--check":
        check()
        return

    do_commit = "--commit" in args
    do_stage  = "--stage"  in args
    do_amend  = "--amend"  in args
    args = [a for a in args if a not in ("--commit", "--stage", "--amend")]
    if do_commit and do_stage:
        sys.exit("Choose one of --stage or --commit, not both.")
    if do_amend and not (do_stage or do_commit):
        sys.exit("--amend requires --stage or --commit.")

    cmd = args[0]
    if cmd not in ("text", "screenshots", "assets", "video", "all", "listing"):
        sys.exit(f"Unknown command '{cmd}'. Use: text, screenshots, assets, video, listing, all")
    # Discover the project's locales from disk before anything else, so a new
    # language works without editing this file.
    _refresh_locales()
    locale = _resolve_locale_arg(args[1] if len(args) > 1 else None)
    if locale is False:
        sys.exit(
            f"Unknown locale '{args[1]}'. "
            f"Known: {', '.join(sorted(l for l, _, _ in LOCALES))}"
        )

    if not (do_commit or do_stage):
        preview(cmd, locale)
        return

    def _apply(edit_id):
        if cmd in ("text", "listing", "all"):
            push_text(edit_id, locale)
        if cmd in ("screenshots", "listing", "all"):
            push_screenshots(edit_id, locale)
        if cmd in ("assets", "all") and not locale:  # artwork is global; skip on single-locale
            push_assets(edit_id)
        if cmd in ("video", "all"):
            push_video(edit_id, locale)

    if do_commit:
        print("⚠️  --commit COMMITS the edit = submits for review AND publishes "
              "(Amazon has no staging gate).\n")
    edit_id = _open_edit(amend=do_amend)
    mode_label = "COMMIT" if do_commit else ("AMEND+STAGE" if do_amend else "STAGE")
    print(f"=== edit {edit_id} ({mode_label}) ===\n")
    try:
        _apply(edit_id)
        if do_commit:
            print("\n── Commit ──")
            _commit_edit(edit_id)
            print("✓ COMMITTED — the edit is submitted for review.")
        elif do_amend:
            print(f"\n✓ AMENDED — assets added to the existing open edit.")
            print(f"  Edit id: {edit_id}")
            print("  The edit remains uncommitted (your upcoming version). Review in the")
            print("  Amazon Developer Console, then Submit there when ready.")
        else:
            print(f"\n✓ STAGED — nothing submitted. This edit is your upcoming version.")
            print(f"  Edit id: {edit_id}")
            print("  Review it in the Amazon Developer Console (the app's open edit /")
            print("  upcoming version), then Submit there. Discard there to abandon.")
            print("\n  ⚠️  Console-only manual steps (API has no endpoint for these):")
            print("     1. Binary File Options → set DRM to Yes (required for API-uploaded APKs)")
            print("     2. App Information → Language Support → check: de, es, fr, it, ja")
    except SystemExit:
        if not do_amend:   # only discard if WE opened the edit
            _discard_edit(edit_id)
            print("✗ failed — edit discarded.")
        else:
            print("✗ failed — the existing edit was NOT discarded (you opened it).")
        raise

if __name__ == "__main__":
    main()
