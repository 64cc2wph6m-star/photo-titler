#!/usr/bin/env python3
"""
Apple Photos Auto-Titler — Streamlit Web UI
============================================
Run with:
    streamlit run photo_titler_app.py

Requirements:
    pip install osxphotos geopy streamlit requests
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
from datetime import date as Date
from pathlib import Path

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# ── Streamlit must be importable before anything else ─────────────────────────
try:
    import streamlit as st
except ImportError:
    print("Streamlit not installed. Run: pip install streamlit")
    sys.exit(1)

# ── Check remaining dependencies ──────────────────────────────────────────────
_MISSING = []
for _pkg in ("osxphotos", "geopy"):
    try:
        __import__(_pkg)
    except ImportError:
        _MISSING.append(_pkg)

# ── File paths ────────────────────────────────────────────────────────────────
HOME            = Path.home()
PROGRESS_FILE   = HOME / ".photo_titler_progress.json"
GEOCACHE_FILE   = HOME / ".photo_titler_geocache.json"
STATUS_FILE     = HOME / ".photo_titler_status.json"
STOP_FILE       = HOME / ".photo_titler_stop"
TITLES_SET_FILE = HOME / ".photo_titler_titles_set.json"   # undo log
DESC_CACHE_FILE = HOME / ".photo_titler_desc_cache.json"   # AI descriptions

# ── US State abbreviations ────────────────────────────────────────────────────
_STATE_ABBREV = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC",
}

_UNKNOWN_PEOPLE = {"_UNKNOWN_", "_Unknown_", "Unknown"}

# ── Geocoder (cached so we don't recreate it on every Streamlit rerun) ────────
@st.cache_resource
def _get_geocoder():
    from geopy.geocoders import Nominatim
    from geopy.extra.rate_limiter import RateLimiter
    geo = Nominatim(user_agent="apple_photos_titler_ui_v2", timeout=10)
    return RateLimiter(geo.reverse, min_delay_seconds=1.2,
                       error_wait_seconds=5, max_retries=3)

# ── Ollama / AI description helpers ──────────────────────────────────────────

OLLAMA_URL = "http://localhost:11434"

_DESCRIBE_PROMPT = (
    "Describe this photo in 5 to 8 words. "
    "Focus on the main subject, activity, or scene. "
    "Be specific and concrete — avoid vague words like 'image' or 'photo'. "
    "Write only the description, no punctuation at the end. "
    "Examples: sunset over mountain lake, children playing soccer in park, "
    "family dinner at restaurant table, dog running on snowy trail"
)


def check_ollama():
    """Returns (is_running, available_models)."""
    if not _HAS_REQUESTS:
        return False, []
    try:
        r = _requests.get(OLLAMA_URL + "/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        return True, models
    except Exception:
        return False, []


def describe_image_ollama(image_path, model="moondream"):
    """
    Ask Ollama to describe an image. Returns a short string or None on failure.
    image_path: str or Path to the image file.
    """
    if not _HAS_REQUESTS:
        return None
    try:
        with open(image_path, "rb") as fh:
            img_b64 = base64.b64encode(fh.read()).decode()
        resp = _requests.post(
            OLLAMA_URL + "/api/generate",
            json={
                "model":  model,
                "prompt": _DESCRIBE_PROMPT,
                "images": [img_b64],
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 30},
            },
            timeout=60,
        )
        raw = resp.json().get("response", "").strip()
        # Clean up common model artifacts
        for prefix in ("The image shows ", "This photo shows ", "This image shows ",
                        "I see ", "There is ", "A photo of "):
            if raw.lower().startswith(prefix.lower()):
                raw = raw[len(prefix):]
        # Capitalise first letter, strip trailing punctuation
        raw = raw.strip(" .,;:")
        if raw:
            raw = raw[0].upper() + raw[1:]
        return raw or None
    except Exception:
        return None


def load_desc_cache():
    if DESC_CACHE_FILE.exists():
        try:
            with open(DESC_CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_desc_cache(cache):
    with open(DESC_CACHE_FILE, "w") as f:
        json.dump(cache, f)


# ── Location helpers ──────────────────────────────────────────────────────────

def _build_location_string(addr, detail):
    """
    detail: "city" | "city_state" | "full"
    """
    parts = []

    if detail == "full":
        for key in ("tourism", "leisure", "natural", "historic", "amenity", "park"):
            val = addr.get(key)
            if val:
                parts.append(val)
                break

    city = (addr.get("city") or addr.get("town") or
            addr.get("village") or addr.get("municipality") or
            addr.get("county"))
    if city:
        parts.append(city)

    if detail in ("city_state", "full"):
        cc    = addr.get("country_code", "").upper()
        state = addr.get("state", "")
        if cc == "US":
            abbrev = _STATE_ABBREV.get(state, state[:2].upper() if state else "")
            if abbrev:
                parts.append(abbrev)
        else:
            country = addr.get("country")
            if country:
                parts.append(country)

    return ", ".join(parts) if parts else None


def get_location_name(lat, lon, cache, detail):
    key = "{}:{}:{}".format(detail, round(lat, 3), round(lon, 3))
    if key in cache:
        return cache[key]
    try:
        reverse = _get_geocoder()
        result  = reverse("{}, {}".format(lat, lon), language="en")
        if result:
            loc = _build_location_string(result.raw.get("address", {}), detail)
            cache[key] = loc
            return loc
    except Exception:
        pass
    cache[key] = None
    return None

# ── People helpers ────────────────────────────────────────────────────────────

def _clean_people(raw, first_name_only):
    people = [p.strip() for p in (raw or [])
              if p and p.strip() and p.strip() not in _UNKNOWN_PEOPLE]
    if first_name_only:
        people = [p.split()[0] for p in people if p]
    return people


def _format_people(people):
    if not people:
        return None
    if len(people) == 1:
        return people[0]
    if len(people) == 2:
        return "{} and {}".format(people[0], people[1])
    return ", ".join(people[:-1]) + ", and {}".format(people[-1])

# ── Title builder ─────────────────────────────────────────────────────────────

def build_title(photo, geocache, cfg, desc_cache=None):
    """
    cfg keys: include_people, include_location, include_date,
              first_name_only, location_detail, date_format,
              use_ai (bool), ai_model (str)
    desc_cache: dict of {uuid: description} — pass None to skip AI lookup.
    """
    parts = []

    if cfg["include_date"] and photo.date:
        parts.append(photo.date.strftime(cfg["date_format"]))

    if cfg["include_people"]:
        people    = _clean_people(list(photo.persons or []), cfg["first_name_only"])
        formatted = _format_people(people)
        if formatted:
            parts.append(formatted)

    if cfg["include_location"] and photo.location:
        lat, lon = photo.location
        if lat is not None and lon is not None:
            loc = get_location_name(lat, lon, geocache, cfg["location_detail"])
            if loc:
                parts.append(loc)

    # If nothing so far and AI is enabled, try an image description
    if not parts and cfg.get("use_ai") and desc_cache is not None:
        uuid = photo.uuid
        if uuid not in desc_cache:
            image_path = photo.path  # None if not downloaded from iCloud
            if image_path and Path(image_path).exists():
                desc = describe_image_ollama(image_path, cfg.get("ai_model", "moondream"))
                desc_cache[uuid] = desc or ""
            else:
                desc_cache[uuid] = ""
        desc = desc_cache.get(uuid, "")
        if desc:
            parts.append(desc)

    return ", ".join(parts) if parts else None

# ── AppleScript title setter ──────────────────────────────────────────────────

def set_photo_title(uuid, title):
    base_uuid = uuid.split("/")[0]

    # Use photoscript (PyObjC-based — no subprocess, safe in multithreaded apps)
    try:
        import photoscript
        photo = photoscript.Photo(base_uuid)
        photo.title = title
        return True, "ok"
    except Exception as exc:
        return False, str(exc)

# ── Persistence helpers ───────────────────────────────────────────────────────

def load_geocache():
    if GEOCACHE_FILE.exists():
        try:
            with open(GEOCACHE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_geocache(cache):
    with open(GEOCACHE_FILE, "w") as f:
        json.dump(cache, f)


def load_progress():
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE) as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_progress(done):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(list(done), f)


def load_titles_set():
    """Returns dict of {uuid: {"title": ..., "filename": ..., "date": ...}}"""
    if TITLES_SET_FILE.exists():
        try:
            with open(TITLES_SET_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_titles_set(titles):
    with open(TITLES_SET_FILE, "w") as f:
        json.dump(titles, f)


def record_title_set(titles_log, uuid, title, filename, date_str):
    """Add an entry to the undo log."""
    titles_log[uuid] = {
        "title":    title,
        "filename": filename,
        "date":     date_str,
    }


def clear_photo_title(uuid):
    """Remove the title from a photo (set to empty string)."""
    base_uuid = uuid.split("/")[0]

    # Use photoscript (PyObjC-based — no subprocess, safe in multithreaded apps)
    try:
        import photoscript
        photo = photoscript.Photo(base_uuid)
        photo.title = ""
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def write_status(data):
    with open(STATUS_FILE, "w") as f:
        json.dump(data, f)


def read_status():
    if STATUS_FILE.exists():
        try:
            with open(STATUS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"running": False, "titled": 0, "no_data": 0, "errors": 0,
            "geocoded": 0, "total": 0, "processed": 0, "current": ""}

# ── Background job ────────────────────────────────────────────────────────────

def _job_worker(cfg, skip_existing, reset_progress):
    """Runs in a daemon thread; writes progress to STATUS_FILE."""
    if reset_progress:
        for f in (PROGRESS_FILE, GEOCACHE_FILE):
            if f.exists():
                f.unlink()
    if STOP_FILE.exists():
        STOP_FILE.unlink()

    stats = {"running": True, "titled": 0, "no_data": 0, "errors": 0,
             "geocoded": 0, "total": 0, "processed": 0,
             "current": "Loading library…"}
    write_status(stats)

    try:
        import osxphotos
        db         = osxphotos.PhotosDB()
        all_photos = db.photos()
        geocache    = load_geocache()
        desc_cache  = load_desc_cache() if cfg.get("use_ai") else {}
        done_uuids  = load_progress()
        titles_log  = load_titles_set()

        to_process = [
            p for p in all_photos
            if p.uuid not in done_uuids
            and not (skip_existing and p.title)
        ]

        stats["total"]   = len(to_process)
        stats["current"] = "Starting…"
        write_status(stats)

        for idx, photo in enumerate(to_process, 1):
            if STOP_FILE.exists():
                stats["current"] = "Stopped by user."
                break

            fname            = photo.original_filename or photo.uuid[:12]
            date_str         = photo.date.strftime("%Y-%m-%d") if photo.date else ""
            stats["current"] = fname
            title            = build_title(photo, geocache, cfg, desc_cache)

            if not title:
                stats["no_data"] += 1
            else:
                ok, _ = set_photo_title(photo.uuid, title)
                if ok:
                    stats["titled"] += 1
                    if cfg["include_location"] and photo.location:
                        lat, lon = photo.location
                        if lat is not None and lon is not None:
                            stats["geocoded"] += 1
                    # Record in undo log
                    record_title_set(titles_log, photo.uuid, title, fname, date_str)
                else:
                    stats["errors"] += 1

            done_uuids.add(photo.uuid)
            stats["processed"] = idx

            if idx % 10 == 0:
                write_status(stats)
            if idx % 100 == 0:
                save_progress(done_uuids)
                save_geocache(geocache)
                save_titles_set(titles_log)
                if cfg.get("use_ai"):
                    save_desc_cache(desc_cache)

        save_progress(done_uuids)
        save_geocache(geocache)
        save_titles_set(titles_log)
        if cfg.get("use_ai"):
            save_desc_cache(desc_cache)
        stats["current"] = "Done!"

    except Exception as exc:
        stats["current"] = "Error: {}".format(exc)

    finally:
        stats["running"] = False
        write_status(stats)
        if STOP_FILE.exists():
            STOP_FILE.unlink()


def start_job(cfg, skip_existing, reset_progress):
    t = threading.Thread(
        target=_job_worker,
        args=(cfg, skip_existing, reset_progress),
        daemon=True,
    )
    t.start()

# ── Preview (runs inline, small sample) ───────────────────────────────────────

def run_preview(cfg, count, skip_existing):
    import osxphotos
    db         = osxphotos.PhotosDB()
    all_photos = db.photos()
    done       = load_progress()

    candidates = [
        p for p in all_photos
        if p.uuid not in done
        and not (skip_existing and p.title)
    ]

    geocache   = load_geocache()
    desc_cache = load_desc_cache() if cfg.get("use_ai") else {}
    rows       = []
    bar        = st.progress(0, text="Loading preview…")

    for i, photo in enumerate(candidates):
        if len(rows) >= count:
            break

        title    = build_title(photo, geocache, cfg, desc_cache)
        people   = _clean_people(list(photo.persons or []), cfg["first_name_only"])
        date_str = photo.date.strftime("%Y-%m-%d") if photo.date else "—"
        has_gps  = "✓" if (photo.location and photo.location[0] is not None) else "—"

        rows.append({
            "File":            photo.original_filename or photo.uuid[:14],
            "Date":            date_str,
            "People tagged":   ", ".join(people) if people else "—",
            "Has GPS":         has_gps,
            "Generated Title": title if title else "⚠ No data (skipped)",
        })

        bar.progress(len(rows) / count,
                     text="Previewing… {}/{}".format(len(rows), count))

    bar.empty()
    save_geocache(geocache)
    if cfg.get("use_ai"):
        save_desc_cache(desc_cache)
    return rows

# ── Progress display widget ────────────────────────────────────────────────────

def show_progress_widget(status):
    total     = status.get("total",     0)
    processed = status.get("processed", 0)
    titled    = status.get("titled",    0)
    no_data   = status.get("no_data",   0)
    errors    = status.get("errors",    0)
    geocoded  = status.get("geocoded",  0)
    current   = status.get("current",  "")
    running   = status.get("running",  False)

    if total > 0:
        pct   = processed / total
        label = "{:,} / {:,} photos ({:.1f}%)".format(processed, total, pct * 100)
        st.progress(pct, text=label)

    if current and running:
        st.caption("Processing: {}".format(current))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("✅ Titled",    "{:,}".format(titled))
    c2.metric("⏭ Skipped",   "{:,}".format(no_data))
    c3.metric("❌ Errors",    "{:,}".format(errors))
    c4.metric("🌍 Geocoded",  "{:,}".format(geocoded))

    if not running and processed > 0:
        pct_titled = (titled / processed * 100) if processed else 0
        st.success(
            "**Job complete!** {:,} photos titled ({:.0f}%), "
            "{:,} skipped, {:,} errors.".format(titled, pct_titled, no_data, errors)
        )

# ── Main Streamlit app ────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Apple Photos Auto-Titler",
        page_icon="📷",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Session state defaults
    if "preview_results" not in st.session_state:
        st.session_state.preview_results = None

    # ── Header ────────────────────────────────────────────────────────────────
    st.title("📷 Apple Photos Auto-Titler")
    st.caption(
        "Automatically set searchable titles on your photo library "
        "based on people, location, and date."
    )

    # ── Dependency check ──────────────────────────────────────────────────────
    if _MISSING:
        st.error(
            "**Missing packages.** Open Terminal and run:\n\n"
            "```\npip install {}\n```".format(" ".join(_MISSING))
        )
        st.stop()

    # ── Sidebar — Configuration ───────────────────────────────────────────────
    with st.sidebar:
        st.header("⚙️ Title Settings")

        # Components
        st.subheader("Include in title")
        include_people   = st.toggle("👤 People names", value=True)
        include_location = st.toggle("📍 Location",     value=True)
        include_date     = st.toggle("📅 Date prefix",  value=False)

        # People format
        first_name_only = False
        if include_people:
            st.subheader("People format")
            ppl_choice      = st.radio("People display format", ["Full name", "First name only"],
                                       index=0, label_visibility="collapsed")
            first_name_only = (ppl_choice == "First name only")

        # Location detail
        location_detail = "city_state"
        if include_location:
            st.subheader("Location detail")
            loc_choice = st.radio(
                "Location detail level",
                ["City only", "City + State / Country", "Full (with landmarks)"],
                index=1, label_visibility="collapsed",
            )
            location_detail = {
                "City only":                 "city",
                "City + State / Country":    "city_state",
                "Full (with landmarks)":     "full",
            }[loc_choice]

        # Date format
        date_format = "%Y-%m-%d"
        if include_date:
            st.subheader("Date format")
            df_choice   = st.radio(
                "Date display format",
                ["2023-06-15  (ISO)", "Jun 2023", "June 15, 2023"],
                index=0, label_visibility="collapsed",
            )
            date_format = {
                "2023-06-15  (ISO)": "%Y-%m-%d",
                "Jun 2023":          "%b %Y",
                "June 15, 2023":     "%B %d, %Y",
            }[df_choice]

        # AI descriptions
        st.divider()
        st.subheader("🤖 AI Descriptions")
        st.caption("Describe photos with no people or GPS using a local AI model (Ollama). Free, private, runs on your Mac.")

        ollama_running, ollama_models = check_ollama()

        if not ollama_running:
            st.warning(
                "Ollama not detected. "
                "[Download Ollama](https://ollama.com) then run:  \n"
                "`ollama pull moondream`"
            )
            use_ai  = False
            ai_model = "moondream"
        else:
            vision_models = [m for m in ollama_models
                             if any(v in m for v in ("moondream", "llava", "bakllava",
                                                      "minicpm", "vision", "cogvlm"))]
            if not vision_models:
                st.warning(
                    "Ollama is running but no vision model found.  \n"
                    "Run: `ollama pull moondream`"
                )
                use_ai   = False
                ai_model = "moondream"
            else:
                st.success("Ollama running ✓")
                use_ai   = st.toggle("Use AI for unidentified photos", value=True)
                ai_model = st.selectbox(
                    "Model", vision_models,
                    index=0,
                    help="moondream = fast & small. llava = slower but richer descriptions.",
                    disabled=not use_ai,
                )

        # Options
        st.divider()
        st.subheader("Options")
        skip_existing = st.toggle("Skip photos with existing title", value=True)

        # Live example
        st.divider()
        st.caption("**Example title:**")
        ex = []
        if include_date:
            ex.append(Date.today().strftime(date_format))
        if include_people:
            ex.append("John Smith" if not first_name_only else "John")
        if include_location:
            ex.append({
                "city":       "Chicago",
                "city_state": "Chicago, IL",
                "full":       "Millennium Park, Chicago, IL",
            }[location_detail])
        if ex:
            st.info(", ".join(ex))
        else:
            st.warning("Enable at least one component above.")

    # ── Build config dict ─────────────────────────────────────────────────────
    cfg = {
        "include_people":   include_people,
        "include_location": include_location,
        "include_date":     include_date,
        "first_name_only":  first_name_only,
        "location_detail":  location_detail,
        "date_format":      date_format,
        "use_ai":           use_ai,
        "ai_model":         ai_model,
    }

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab_prev, tab_run, tab_prog, tab_undo, tab_about = st.tabs(
        ["🔍 Preview", "▶️ Run", "📊 Progress", "↩️ Undo", "ℹ️ About"]
    )

    # ── PREVIEW TAB ───────────────────────────────────────────────────────────
    with tab_prev:
        st.subheader("Preview generated titles")
        st.caption(
            "See what titles would be generated — no changes are made to your library."
        )

        col_a, col_b = st.columns([1, 3])
        with col_a:
            n = st.slider("Sample size", min_value=5, max_value=100,
                          value=25, step=5)
            if st.button("🔍 Generate Preview", type="primary",
                         use_container_width=True):
                with st.spinner("Generating preview…"):
                    st.session_state.preview_results = run_preview(cfg, n, skip_existing)

        if st.session_state.preview_results:
            rows   = st.session_state.preview_results
            titled = sum(1 for r in rows
                         if not r["Generated Title"].startswith("⚠"))
            st.success(
                "**{} of {} photos** would get a title.".format(titled, len(rows))
            )
            st.dataframe(
                rows,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Generated Title": st.column_config.TextColumn(width="large"),
                    "File":            st.column_config.TextColumn(width="medium"),
                    "Date":            st.column_config.TextColumn(width="small"),
                    "People tagged":   st.column_config.TextColumn(width="medium"),
                    "Has GPS":         st.column_config.TextColumn(width="small"),
                },
            )

    # ── RUN TAB ───────────────────────────────────────────────────────────────
    with tab_run:
        st.subheader("Run on your full library")
        status     = read_status()
        is_running = status.get("running", False)

        if is_running:
            st.warning("⏳ A job is currently running.")
            st.divider()
            show_progress_widget(status)
            st.divider()
            if st.button("⏹ Stop Job", type="secondary"):
                STOP_FILE.touch()
                st.info("Stop signal sent. The job will stop after the current photo.")
            time.sleep(3)
            st.rerun()
        else:
            done_count = len(load_progress())
            c1, c2 = st.columns(2)
            with c1:
                reset = st.checkbox(
                    "Reset progress (start over from photo 1)", value=False
                )
            with c2:
                if done_count > 0:
                    st.caption(
                        "**{:,}** photos already processed — "
                        "the job will continue from where it left off.".format(done_count)
                    )

            st.info(
                "Make sure **Apple Photos is open** before starting. "
                "The job runs in the background — you can switch tabs or use your Mac normally. "
                "Progress is saved every 100 photos, so you can stop and restart anytime."
            )

            if not (include_people or include_location or include_date):
                st.error("No title components selected. Enable at least one in the sidebar.")
            elif st.button("▶️ Run Full Library", type="primary",
                           use_container_width=True):
                start_job(cfg, skip_existing, reset)
                time.sleep(1)   # give thread a moment to write initial status
                st.rerun()

    # ── PROGRESS TAB ─────────────────────────────────────────────────────────
    with tab_prog:
        st.subheader("Job progress")
        status = read_status()

        if status.get("total", 0) > 0:
            show_progress_widget(status)
            if status.get("running"):
                if st.button("🔄 Refresh"):
                    st.rerun()
        else:
            st.info("No job has been run yet. Go to the **▶️ Run** tab to start.")

    # ── UNDO TAB ──────────────────────────────────────────────────────────────
    with tab_undo:
        st.subheader("↩️ Undo — Remove titles set by this app")
        st.caption(
            "This removes titles from every photo this app has titled. "
            "Titles you set manually in Photos are not affected."
        )

        titles_log = load_titles_set()
        count      = len(titles_log)

        if count == 0:
            st.info("No titles have been set by this app yet — nothing to undo.")
        else:
            st.metric("Photos titled by this app", "{:,}".format(count))

            # Show a sample of what will be cleared
            with st.expander("Preview titles that will be removed ({:,} total)".format(count)):
                sample = list(titles_log.items())[:200]
                rows   = [
                    {"File": v["filename"], "Date": v["date"], "Title (will be removed)": v["title"]}
                    for _, v in sample
                ]
                st.dataframe(rows, use_container_width=True, hide_index=True)
                if count > 200:
                    st.caption("Showing first 200 of {:,}".format(count))

            st.divider()

            col1, col2 = st.columns(2)
            with col1:
                also_reset = st.checkbox(
                    "Also reset progress (allows re-titling after undo)",
                    value=True,
                )
            with col2:
                st.caption(
                    "If checked, the app will re-process all photos next time you run, "
                    "so you can start fresh with different settings."
                )

            st.warning(
                "**This will clear {:,} photo titles.** "
                "Apple Photos will re-index automatically.".format(count)
            )

            if "undo_confirmed" not in st.session_state:
                st.session_state.undo_confirmed = False

            if not st.session_state.undo_confirmed:
                if st.button("↩️ Clear All Titles", type="secondary",
                             use_container_width=True):
                    st.session_state.undo_confirmed = True
                    st.rerun()
            else:
                st.error("Are you sure? This cannot be undone.")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("✅ Yes, clear all titles", type="primary",
                                 use_container_width=True):
                        bar      = st.progress(0, text="Clearing titles…")
                        cleared  = 0
                        errors   = 0
                        total_u  = len(titles_log)
                        uuids    = list(titles_log.keys())

                        for i, uuid in enumerate(uuids, 1):
                            ok, _ = clear_photo_title(uuid)
                            if ok:
                                cleared += 1
                            else:
                                errors += 1
                            bar.progress(i / total_u,
                                         text="Clearing… {:,}/{:,}".format(i, total_u))

                        # Clear the log
                        save_titles_set({})
                        if also_reset:
                            if PROGRESS_FILE.exists():
                                PROGRESS_FILE.unlink()

                        bar.empty()
                        st.session_state.undo_confirmed = False
                        st.success(
                            "Done! {:,} titles cleared, {:,} errors. "
                            "Photos will re-index shortly.".format(cleared, errors)
                        )
                with c2:
                    if st.button("❌ Cancel", use_container_width=True):
                        st.session_state.undo_confirmed = False
                        st.rerun()

    # ── ABOUT TAB ────────────────────────────────────────────────────────────
    with tab_about:
        st.subheader("How it works")
        st.markdown("""
This tool sets the **title** field on each photo in Apple Photos, making your
library searchable by people, place, and date.

**Data sources:**
- 👤 **People** — names read from Apple Photos' built-in face recognition
  (any faces you've already named in Photos)
- 📍 **Location** — GPS coordinates embedded in the photo, reverse-geocoded
  to a human-readable place name using OpenStreetMap (free, no API key needed)
- 📅 **Date** — extracted from the photo's EXIF metadata

**Where the title appears in Apple Photos:**
Open any photo → tap the **ⓘ** button → the title shows at the top.
It's also fully indexed by the Photos search bar — search "Paris" or "John"
and matching photos will appear.

**Time estimates:**

| Library size | Approx. time |
|---|---|
| 1,000 photos | ~20 minutes |
| 5,000 photos | ~1.5 hours |
| 10,000 photos | ~3 hours |
| 34,000 photos | ~9 hours |

The geocoding step (GPS → place name) is rate-limited to 1 request/second by
OpenStreetMap. Results are cached, so photos from the same area only geocode once.

**Photos that get skipped:**
Photos with no GPS data AND no tagged faces have nothing to build a title from,
so they're skipped. Progress is saved so you can stop and restart at any time.

**Sharing / open source:**
This app runs entirely on your Mac — no data is sent anywhere except to
OpenStreetMap's free geocoding API for GPS → place name lookups.
        """)


if __name__ == "__main__":
    main()
