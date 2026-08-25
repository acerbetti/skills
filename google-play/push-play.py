#!/usr/bin/env python3
"""
push-play.py — Push Google Play store-listing metadata + screenshots from local files.

Play uses an EDIT TRANSACTION: insert an edit → stage changes → validate → commit.
By default this script is DRY-RUN (validate only, then discards the edit); pass
--commit to actually commit. With Managed Publishing ON in Play Console, a commit
stages changes for you to publish manually in the Console (it does NOT go live).

Usage:
  python3 push-play.py --check                 # verify service account + edit access
  python3 push-play.py all [--commit]          # listing text + screenshots + assets
  python3 push-play.py text [locale] [--commit]        # title/short/full description
  python3 push-play.py screenshots [locale] [--commit] # phone/10-inch/tv images (replace)
  python3 push-play.py assets [--commit]               # icon, feature graphic, TV banner

Scope: store listing only. Release notes are owned by CI (track releases); this
script never touches tracks, versions, or the AAB.

Required env var:
  PLAY_SERVICE_ACCOUNT_JSON   path to the Google service-account JSON key

Deps:  google-api-python-client google-auth   (install into the skill venv)
"""

import os, re, sys, glob

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PACKAGE = "com.caplaz.clock"
SCOPES  = ["https://www.googleapis.com/auth/androidpublisher"]

META_DIR = os.environ.get(
    "PLAY_META_DIR",
    os.path.join(os.getcwd(), "store-listings", "google-play"),
)
# Screenshots come from the Android pipeline output (app-source artwork), per locale.
SCREENSHOT_TEMPLATE = os.environ.get(
    "PLAY_SCREENSHOT_TEMPLATE",
    os.path.join(os.getcwd(), "android-native", "screenshots", "captioned", "{sdir}"),
)

# (Play listing language, metadata file relative to META_DIR, screenshot dir code)
# NB: Play locale codes carry a region (it-IT, ja-JP) and Simplified Chinese is
# zh-CN — while the screenshot pipeline emits a zh-Hans folder. This table bridges
# all three naming schemes.
LOCALES = [
    ("en-US", "metadata.md",   "en"),
    ("de-DE", "i18n/de.md",    "de"),
    ("fr-FR", "i18n/fr.md",    "fr"),
    ("es-ES", "i18n/es-ES.md", "es-ES"),
    ("it-IT", "i18n/it.md",    "it"),
    ("ja-JP", "i18n/ja.md",    "ja"),
    ("zh-CN", "i18n/zh-CN.md", "zh-Hans"),
]

# MD heading prefix → Play listing field
TEXT_FIELDS = [
    ("App name",          "title"),
    ("Short description", "shortDescription"),
    ("Full description",  "fullDescription"),
]

# Android capture prefix → Play image type (tablet → 10-inch by decision)
SCREENSHOT_TYPES = [
    ("phoneScreenshots",   "phone_*.png"),
    ("tenInchScreenshots", "tablet_*.png"),
    ("tvScreenshots",      "tv_*.png"),
]

# Listing-level artwork (uploaded to the default language en-US)
ASSETS = [
    ("icon",           "assets/icon-512.png"),
    ("featureGraphic", "assets/feature-graphic-1024x500.png"),
    ("tvBanner",       "assets/android-tv-banner-1280x720.png"),
]

# ---------------------------------------------------------------------------
# Auth / service
# ---------------------------------------------------------------------------

def _service():
    path = os.environ.get("PLAY_SERVICE_ACCOUNT_JSON")
    if not path:
        sys.exit("✗ PLAY_SERVICE_ACCOUNT_JSON is not set")
    if not os.path.exists(path):
        sys.exit(f"✗ service account JSON not found: {path}")
    creds = service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    return build("androidpublisher", "v3", credentials=creds, cache_discovery=False)

# ---------------------------------------------------------------------------
# Markdown helpers (fields are top-level ### headings; first fenced block wins)
# ---------------------------------------------------------------------------

def _load_md(rel_path: str) -> str:
    return open(os.path.join(META_DIR, rel_path), encoding="utf-8").read()

def _field(text: str, prefix: str):
    m = re.search(rf"(?ms)^### {re.escape(prefix)}[^\n]*\n.*?^```\n(.*?)\n```", text)
    return m.group(1).strip() if m else None

_VIDEO_URL = None
def _promo_video_url():
    """The listing 'video' field is a YouTube URL — Play does NOT host video files, so
    the .mp4 must be uploaded to YouTube first and its URL pasted into the en metadata's
    '### Promo video' block. Same URL for every locale. Returns None until a real
    http(s) URL is present (so a placeholder is a harmless no-op)."""
    global _VIDEO_URL
    if _VIDEO_URL is None:
        url = _field(_load_md("metadata.md"), "Promo video") or ""
        _VIDEO_URL = url if url.startswith(("http://", "https://")) else ""
    return _VIDEO_URL

def _screenshot_files(sdir: str, pattern: str) -> list:
    d = SCREENSHOT_TEMPLATE.format(sdir=sdir)
    return sorted(glob.glob(os.path.join(d, pattern)))

# ---------------------------------------------------------------------------
# Edit operations
# ---------------------------------------------------------------------------

def push_text(svc, edit_id, only_locale):
    print("── Listing text ──")
    for play_loc, md_rel, _ in LOCALES:
        if only_locale and play_loc != only_locale:
            continue
        text = _load_md(md_rel)
        body = {}
        for prefix, field in TEXT_FIELDS:
            val = _field(text, prefix)
            if val is not None:
                body[field] = val
        vurl = _promo_video_url()          # YouTube URL, same for every locale
        if vurl:
            body["video"] = vurl
        if not body:
            print(f"  [{play_loc}] no fields found — skip")
            continue
        svc.edits().listings().update(
            packageName=PACKAGE, editId=edit_id, language=play_loc, body=body,
        ).execute(num_retries=5)
        print(f"  [{play_loc}] ✓ {', '.join(body)}")

def push_screenshots(svc, edit_id, only_locale):
    print("── Screenshots (replace) ──")
    for play_loc, _, sdir in LOCALES:
        if only_locale and play_loc != only_locale:
            continue
        for image_type, pattern in SCREENSHOT_TYPES:
            files = _screenshot_files(sdir, pattern)
            if not files:
                continue
            svc.edits().images().deleteall(
                packageName=PACKAGE, editId=edit_id, language=play_loc, imageType=image_type,
            ).execute(num_retries=5)
            for f in files:
                svc.edits().images().upload(
                    packageName=PACKAGE, editId=edit_id, language=play_loc,
                    imageType=image_type,
                    media_body=MediaFileUpload(f, mimetype="image/png"),
                ).execute(num_retries=5)
            print(f"  [{play_loc}] {image_type}: {len(files)} from {sdir}/")

def push_assets(svc, edit_id):
    print("── Listing artwork (en-US) ──")
    for image_type, rel in ASSETS:
        path = os.path.join(META_DIR, rel)
        if not os.path.exists(path):
            print(f"  {image_type}: missing {rel} — skip")
            continue
        svc.edits().images().deleteall(
            packageName=PACKAGE, editId=edit_id, language="en-US", imageType=image_type,
        ).execute(num_retries=5)
        svc.edits().images().upload(
            packageName=PACKAGE, editId=edit_id, language="en-US", imageType=image_type,
            media_body=MediaFileUpload(path, mimetype="image/png"),
        ).execute(num_retries=5)
        print(f"  ✓ {image_type} ← {os.path.basename(rel)}")

# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

def check():
    print("── Service account ──")
    path = os.environ.get("PLAY_SERVICE_ACCOUNT_JSON")
    if not path or not os.path.exists(path):
        sys.exit(f"  ✗ PLAY_SERVICE_ACCOUNT_JSON missing or not found: {path}")
    import json
    info = json.load(open(path))
    print(f"  ✓ {path}")
    print(f"    client_email: {info.get('client_email')}")

    svc = _service()
    print("\n── Edit access ──")
    try:
        edit = svc.edits().insert(packageName=PACKAGE, body={}).execute(num_retries=5)
        eid = edit["id"]
    except HttpError as e:
        print(f"  ✗ edits.insert failed: {e.resp.status} {e.error_details}")
        print("    The service account needs 'Edit store listing, pricing & distribution'")
        print("    granted for this app in Play Console → Users and permissions.")
        sys.exit(1)
    try:
        listings = svc.edits().listings().list(packageName=PACKAGE, editId=eid).execute(num_retries=5)
        langs = sorted(l["language"] for l in listings.get("listings", []))
        print(f"  ✓ edit {eid} created; existing listing languages: {', '.join(langs) or '(none)'}")
    finally:
        svc.edits().delete(packageName=PACKAGE, editId=eid).execute(num_retries=5)
        print("  ✓ test edit discarded")
    print("\n✓ Auth + edit access OK. Ready to push (dry-run by default; --commit to stage).")

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
    args = [a for a in args if a != "--commit"]

    cmd = args[0]
    locale = args[1] if len(args) > 1 else None
    valid_locales = {p for p, _, _ in LOCALES}
    if locale and locale not in valid_locales:
        sys.exit(f"Unknown locale '{locale}'. Known: {', '.join(sorted(valid_locales))}")

    svc = _service()
    edit_id = svc.edits().insert(packageName=PACKAGE, body={}).execute(num_retries=5)["id"]
    print(f"=== edit {edit_id} ({'COMMIT' if do_commit else 'dry-run'}) ===\n")

    try:
        if cmd == "text":
            push_text(svc, edit_id, locale)
        elif cmd == "screenshots":
            push_screenshots(svc, edit_id, locale)
        elif cmd == "assets":
            push_assets(svc, edit_id)
        elif cmd == "all":
            push_text(svc, edit_id, locale)
            push_screenshots(svc, edit_id, locale)
            if not locale:  # artwork is en-US-global; don't touch it for a single-locale run
                push_assets(svc, edit_id)
        else:
            svc.edits().delete(packageName=PACKAGE, editId=edit_id).execute(num_retries=5)
            sys.exit(f"Unknown command '{cmd}'. Use: text, screenshots, assets, all")

        print("\n── Validate ──")
        svc.edits().validate(packageName=PACKAGE, editId=edit_id).execute(num_retries=5)
        print("  ✓ edit is valid")

        if do_commit:
            svc.edits().commit(packageName=PACKAGE, editId=edit_id).execute(num_retries=5)
            print("\n✓ COMMITTED. With Managed Publishing ON, publish it in Play Console.")
        else:
            svc.edits().delete(packageName=PACKAGE, editId=edit_id).execute(num_retries=5)
            print("\n✓ Dry-run OK — edit discarded. Re-run with --commit to stage the change.")
    except HttpError as e:
        # Leave nothing dangling on failure.
        try:
            svc.edits().delete(packageName=PACKAGE, editId=edit_id).execute(num_retries=5)
        except Exception:
            pass
        print(f"\n✗ API error: {e.resp.status} {e.error_details}\n{e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
