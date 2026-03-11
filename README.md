# YouTube Daily Digest

Automatically finds a YouTube video on your chosen topic every day, transcribes it, summarizes it with Claude AI, and emails the summary to you.

---

## Folder structure

```
youtube-daily-digest/
├── app.py            ← Flask backend (all logic lives here)
├── index.html        ← Settings & log UI (single-page)
├── requirements.txt  ← Python dependencies
├── start.bat         ← One-click launcher for Windows
├── settings.json     ← Created automatically on first save
└── logs.json         ← Created automatically
```

---

## Quick start (Windows)

### 1. Install Python
Download Python 3.10+ from https://www.python.org/downloads/
During install: **check "Add Python to PATH"**.

### 2. Get API keys (see section below)

### 3. Run the app
Double-click **`start.bat`**.
It will:
- Create a virtual environment
- Install all dependencies
- Open http://localhost:5000 in your browser

### 4. Configure in the browser
Fill in all fields on the settings page and click **Save Settings**.
Then click **▶ Run Now** to test immediately.

---

## Getting API keys

### YouTube Data API v3 (free)

1. Go to https://console.cloud.google.com/
2. Create a new project (or use an existing one).
3. In the left menu → **APIs & Services** → **Library**.
4. Search for **"YouTube Data API v3"** → click it → **Enable**.
5. Go to **APIs & Services** → **Credentials** → **+ Create Credentials** → **API key**.
6. Copy the key (looks like `AIzaSy…`).
7. Optional: click **Restrict Key** → under "API restrictions" select "YouTube Data API v3" for security.

Free quota: **10,000 units/day** (a search costs 100 units — so 100 free searches/day, plenty for this app).

---

### Claude API key (Anthropic)

1. Go to https://console.anthropic.com/
2. Sign up / log in.
3. Click your name → **API Keys** → **+ Create Key**.
4. Copy the key (looks like `sk-ant-…`).
5. Add credits ($5 minimum) in the Billing section.

Cost per digest: roughly **$0.001–0.005** (a few tenths of a cent), so $5 lasts years.

---

### Gmail App Password (for sending email)

Your regular Gmail password **will not work**. You need an App Password.

1. Go to your Google Account: https://myaccount.google.com/security
2. Make sure **2-Step Verification** is ON (required).
3. Search for **"App Passwords"** in the search bar (or go to https://myaccount.google.com/apppasswords).
4. Under "Select app" choose **Mail**, under "Select device" choose **Windows Computer**.
5. Click **Generate** → copy the 16-character password (e.g. `abcd efgh ijkl mnop`).
6. Paste it into the **Gmail App Password** field in the app (spaces are fine, they're stripped automatically).

---

## How it works

```
Every day at your scheduled time:

1. Search YouTube Data API v3
   └─ Query: your topic, ordered by relevance, preferring recent videos

2. Find first video with accessible captions
   └─ Tries manual captions first, then auto-generated
   └─ Supports any language (EN, ES, FR, DE, PT, IT, JA, KO, ZH, AR, RU, HI, …)

3. Summarize with Claude claude-sonnet-4-6
   └─ 5–10 bullet points in the video's own language

4. Send HTML email via Gmail SMTP
   └─ Contains: title, channel, YouTube link, bullet summary
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError` | Run `start.bat` again (it re-installs dependencies) |
| YouTube API error 403 | API key is wrong or YouTube Data API v3 not enabled |
| "No usable transcript" | The found video has no captions — app tries up to 15 videos automatically |
| Gmail login failed | You used your regular password; create an App Password instead |
| Email not received | Check spam folder; also verify recipient email in settings |
| App closes immediately | Open a regular CMD window and run `python app.py` to see the error |

---

## Running in the background (optional)

To keep the app running after you close the terminal, use **Task Scheduler**:

1. Open **Task Scheduler** → **Create Basic Task**.
2. Trigger: **At log on** (or at startup).
3. Action: **Start a program** → browse to `start.bat`.
4. Finish.

The app will auto-start and the daily email will be sent even if you don't open the browser.
