# 📷 PhotoTitler

**Automatically title your Apple Photos library using location, date, and the people in each photo.**

PhotoTitler scans your Apple Photos library and writes descriptive titles like:

> `Chicago, IL · Jun 2019 · Sarah, James`  
> `Maui, HI · Dec 2022`  
> `Home · Mar 2024 · Family Reunion`

It runs as a local web app on your Mac — no cloud, no subscriptions, your photos never leave your machine.

---

## ✨ Features

- Titles based on **GPS location** (city, state, country)
- Includes **date** (month + year)
- Includes **named people** from your Photos face tags
- Optional **AI descriptions** via [Ollama](https://ollama.ai) (e.g. "sunset over mountain lake")
- Pause, resume, and undo — non-destructive, titles can be cleared any time
- Skips photos that already have titles
- Works on libraries with tens of thousands of photos

---

## Requirements

- **macOS** (Apple Silicon or Intel)
- **Python 3.9+** — [download here](https://www.python.org/downloads/)
- **Apple Photos** with your library open
- Internet connection (for reverse geocoding — no API key required)

---

## Installation

### Step 1 — Install Python packages

Open **Terminal** and run:

```bash
pip3 install streamlit osxphotos geopy photoscript requests
```

### Step 2 — Download the files

Download these two files from this repo and save them to your **home folder** (`/Users/yourname/`):

- `photo_titler_app.py`
- `build_mac_app.sh`

### Step 3 — Build the Mac app

In Terminal, run:

```bash
bash ~/build_mac_app.sh
```

This creates **PhotoTitler.app** in your `/Applications` folder.

---

## Running PhotoTitler

1. Open **Finder → Applications** and double-click **PhotoTitler**
2. Your browser opens automatically to `http://localhost:8501`
3. Choose your titling options and click **▶ Run**

> **Tip:** Drag PhotoTitler to your Dock for one-click access.

---

## How Titles Are Built

Each title combines available information in this order:

```
Location · Month Year · Person1, Person2
```

- **Location** — reverse-geocoded from GPS coordinates (city + state for US, city + country elsewhere). Photos without GPS are titled by date only.
- **Date** — month and year the photo was taken
- **People** — named faces recognized by Apple Photos (unnamed faces are skipped)

---

## Optional: AI Descriptions with Ollama

If you have [Ollama](https://ollama.ai) installed with a vision model (e.g. `moondream`), PhotoTitler can generate scene descriptions for photos without GPS:

```bash
# Install Ollama, then pull a vision model
ollama pull moondream
```

Enable the **AI Descriptions** toggle in the app UI.

---

## Undoing Titles

The app keeps a log of every title it sets. To clear all titles PhotoTitler has written:

1. Open PhotoTitler
2. Click **↩ Undo All Titles**

This only removes titles PhotoTitler wrote — any titles you set manually are untouched.

---

## Troubleshooting

**App opens but browser shows "can't connect"**
- Make sure Chrome is installed (PhotoTitler opens Chrome by default)
- Try opening `http://localhost:8501` manually in Chrome

**Job seems stuck**
- Check the log: open Terminal and run `tail -f /tmp/photo_titler.log`
- If needed, reset: `echo '{"running": false}' > ~/.photo_titler_status.json`

**Python crashed**
- Make sure you're using the `photoscript` method (not osascript subprocess). The latest version of the app handles this automatically.

**Photos titles aren't saving**
- Make sure Apple Photos is open and you've granted accessibility permissions if prompted

---

## Feedback & Bug Reports

This is an early beta — your feedback shapes what comes next.

👉 [Open a feedback issue](../../issues/new?template=feedback.md)  
🐛 [Report a bug](../../issues/new?template=bug_report.md)

---

## License

MIT — use it, modify it, share it.
