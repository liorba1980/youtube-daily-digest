"""
YouTube Daily Digest - Multi-user Flask Backend
Each user has their own settings, logs, and schedule.
"""

import json
import os
import smtplib
import threading
import time
import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic
import requests
from flask import Flask, jsonify, request, send_file, redirect, url_for
from flask_cors import CORS
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from youtube_transcript_api import YouTubeTranscriptApi
try:
    from youtube_transcript_api._errors import (
        NoTranscriptFound, TranscriptsDisabled, VideoUnavailable,
    )
except ImportError:
    from youtube_transcript_api import (
        NoTranscriptFound, TranscriptsDisabled, VideoUnavailable,
    )

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-before-hosting")
CORS(app)

# ── Storage paths (override DATA_DIR on Railway with a volume mount) ──────────

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "users.db")

# ── Database ──────────────────────────────────────────────────────────────────

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login_page"


class User(UserMixin, db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ── Per-user data files ───────────────────────────────────────────────────────

def user_dir(user_id: int) -> str:
    d = os.path.join(DATA_DIR, str(user_id))
    os.makedirs(d, exist_ok=True)
    return d

def settings_path(user_id: int) -> str:
    return os.path.join(user_dir(user_id), "settings.json")

def logs_path(user_id: int) -> str:
    return os.path.join(user_dir(user_id), "logs.json")


DEFAULT_SETTINGS = {
    "topics": ["", "", "", "", ""],
    "topic_index": 0,
    "email": "",
    "send_time": "08:00",
    "youtube_api_key": "",
    "claude_api_key": "",
    "gmail_user": "",
    "gmail_app_password": "",
    "translate_to_hebrew": False,
}


def load_settings(user_id: int) -> dict:
    path = settings_path(user_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {**DEFAULT_SETTINGS, **data}
    return DEFAULT_SETTINGS.copy()


def save_settings(user_id: int, settings: dict) -> None:
    with open(settings_path(user_id), "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def load_logs(user_id: int) -> list:
    path = logs_path(user_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def add_log(user_id: int, message: str, status: str = "info") -> None:
    logs = load_logs(user_id)
    logs.insert(0, {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": message,
        "status": status,
    })
    logs = logs[:100]
    with open(logs_path(user_id), "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)
    print(f"[User {user_id}][{status.upper()}] {message}")


def get_active_topic(settings: dict) -> str | None:
    topics = [t.strip() for t in settings.get("topics", []) if t.strip()]
    if not topics:
        return None
    idx = settings.get("topic_index", 0) % len(topics)
    return topics[idx]


def advance_topic(user_id: int, settings: dict) -> None:
    topics = [t.strip() for t in settings.get("topics", []) if t.strip()]
    if len(topics) <= 1:
        return
    settings["topic_index"] = (settings.get("topic_index", 0) + 1) % len(topics)
    save_settings(user_id, settings)


# ── Core logic ────────────────────────────────────────────────────────────────

def search_youtube(api_key: str, topic: str) -> list:
    url = "https://www.googleapis.com/youtube/v3/search"
    three_days_ago = (
        datetime.datetime.utcnow() - datetime.timedelta(days=3)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "part": "snippet", "q": topic, "type": "video",
        "order": "relevance", "publishedAfter": three_days_ago,
        "maxResults": 15, "key": api_key, "relevanceLanguage": "en",
    }
    resp = requests.get(url, params=params, timeout=15)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"YouTube API error: {data['error']['message']}")
    items = data.get("items", [])
    if not items:
        params.pop("publishedAfter")
        params["order"] = "date"
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"YouTube API error: {data['error']['message']}")
        items = data.get("items", [])
    return items


def get_transcript(video_id: str) -> tuple[str, str]:
    api = YouTubeTranscriptApi()
    fetched = api.fetch(video_id)
    snippets = fetched.snippets if hasattr(fetched, "snippets") else list(fetched)
    text = " ".join(s.text for s in snippets).strip()
    lang = getattr(fetched, "language_code", "en")
    if not text:
        raise ValueError("Transcript is empty")
    return text, lang


def summarize_with_claude(api_key: str, transcript: str, title: str, language: str,
                          translate_to_hebrew: bool = False) -> str:
    client = anthropic.Anthropic(api_key=api_key)
    MAX_CHARS = 18_000
    if len(transcript) > MAX_CHARS:
        transcript = transcript[:MAX_CHARS] + "\n\n[… transcript trimmed for length]"
    if translate_to_hebrew:
        lang_note = "Respond in Hebrew (עברית). Translate and summarize everything in Hebrew."
    elif language.startswith("en"):
        lang_note = "Respond in English."
    else:
        lang_note = f"Respond in the same language as the video (language code: {language})."
    prompt = f"""You are summarizing a YouTube video for a daily digest newsletter.
{lang_note}

Video title: {title}

Transcript:
{transcript}

Write exactly 5–10 bullet points that capture the key insights, facts, and takeaways.
Start each bullet with "•". Be clear, informative, and concise."""
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def send_email(gmail_user: str, gmail_password: str, recipient: str,
               subject: str, html_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"YouTube Daily Digest <{gmail_user}>"
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, recipient, msg.as_string())


def build_email_html(title, channel, video_url, summary, topic, language,
                     date_str, topic_num, topic_total, translate_to_hebrew=False):
    lines = [l.strip() for l in summary.splitlines() if l.strip()]
    bullets_html = ""
    for line in lines:
        clean = line.lstrip("•·*-– ").strip()
        if clean:
            bullets_html += f"<li>{clean}</li>\n"
    rtl_style = "direction:rtl;text-align:right;" if translate_to_hebrew else ""
    html_lang = "he" if translate_to_hebrew else "en"
    return f"""<!DOCTYPE html>
<html lang="{html_lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>YouTube Daily Digest</title>
  <style>
    body{{margin:0;padding:0;background:#f4f4f5;font-family:'Segoe UI',Arial,sans-serif;color:#222}}
    .wrap{{max-width:620px;margin:30px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.10)}}
    .header{{background:#ff0000;padding:28px 24px;text-align:center}}
    .header h1{{margin:0;color:#fff;font-size:22px;letter-spacing:.5px}}
    .header p{{margin:6px 0 0;color:#ffcccc;font-size:13px}}
    .badge{{display:inline-block;background:rgba(255,255,255,.25);color:#fff;padding:3px 12px;border-radius:20px;font-size:12px;margin-top:8px}}
    .body{{padding:24px}}
    .card{{background:#fafafa;border:1px solid #eee;border-radius:8px;padding:18px;margin-bottom:18px}}
    .card h2{{margin:0 0 12px;font-size:15px;color:#ff0000}}
    .card p{{margin:6px 0;font-size:14px;line-height:1.6}}
    .card a{{color:#ff0000;text-decoration:none;font-weight:600}}
    ul{{margin:0;padding-left:20px}}
    li{{font-size:14px;line-height:1.7;margin-bottom:7px}}
    .footer{{text-align:center;padding:16px;font-size:11px;color:#aaa;border-top:1px solid #f0f0f0}}
  </style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>&#x1F4FA; YouTube Daily Digest</h1>
    <p>{date_str}</p>
    <span class="badge">Topic {topic_num}/{topic_total}: {topic}</span>
  </div>
  <div class="body">
    <div class="card">
      <h2>&#x1F3AC; Today's Video</h2>
      <p><strong>{title}</strong></p>
      <p>&#x1F4FA; Channel: <em>{channel}</em></p>
      <p>&#x1F310; Language: <code>{language}</code></p>
      <p>&#x1F517; <a href="{video_url}" target="_blank">Watch on YouTube &rarr;</a></p>
    </div>
    <div class="card" style="{rtl_style}">
      <h2>&#x1F4DD; AI-Generated Summary</h2>
      <ul>
        {bullets_html}
      </ul>
    </div>
  </div>
  <div class="footer">
    Generated automatically by YouTube Daily Digest &bull; Powered by Claude AI
  </div>
</div>
</body>
</html>"""


# ── Main job ──────────────────────────────────────────────────────────────────

def run_daily_job(user_id: int) -> None:
    settings = load_settings(user_id)
    required = {
        "youtube_api_key":    "YouTube Data API key",
        "claude_api_key":     "Claude API key",
        "gmail_user":         "Gmail address",
        "gmail_app_password": "Gmail App Password",
        "email":              "recipient email",
    }
    for field, label in required.items():
        if not settings.get(field):
            add_log(user_id, f"Missing required setting: {label}", "error")
            return

    topic = get_active_topic(settings)
    if not topic:
        add_log(user_id, "No topics configured.", "error")
        return

    all_topics = [t.strip() for t in settings.get("topics", []) if t.strip()]
    topic_num  = (settings.get("topic_index", 0) % len(all_topics)) + 1
    add_log(user_id, f"Starting daily job — topic {topic_num}/{len(all_topics)}: «{topic}»", "info")

    try:
        add_log(user_id, "Searching YouTube…", "info")
        videos = search_youtube(settings["youtube_api_key"], topic)
        if not videos:
            add_log(user_id, "No videos found for this topic.", "error")
            return

        chosen = None
        transcript_text = ""
        lang_code = "en"
        for video in videos:
            vid_id      = video["id"]["videoId"]
            vid_title   = video["snippet"]["title"]
            vid_channel = video["snippet"]["channelTitle"]
            add_log(user_id, f"Checking captions for: {vid_title}", "info")
            try:
                transcript_text, lang_code = get_transcript(vid_id)
                chosen = {
                    "id": vid_id, "title": vid_title, "channel": vid_channel,
                    "url": f"https://www.youtube.com/watch?v={vid_id}",
                }
                break
            except Exception as e:
                add_log(user_id, f"Transcript failed for «{vid_title}»: {e}", "warning")

        if not chosen:
            add_log(user_id, "None of the returned videos had accessible captions.", "error")
            return

        add_log(user_id, f"Selected: {chosen['title']} (lang={lang_code})", "info")
        add_log(user_id, "Generating summary with Claude…", "info")
        summary = summarize_with_claude(
            settings["claude_api_key"], transcript_text, chosen["title"], lang_code,
            translate_to_hebrew=settings.get("translate_to_hebrew", False),
        )

        date_str = datetime.date.today().strftime("%B %d, %Y")
        subject  = f"📺 Daily Digest: {topic.title()} — {date_str}"
        html = build_email_html(
            chosen["title"], chosen["channel"], chosen["url"],
            summary, topic, lang_code, date_str, topic_num, len(all_topics),
            translate_to_hebrew=settings.get("translate_to_hebrew", False),
        )

        add_log(user_id, f"Sending email to {settings['email']}…", "info")
        send_email(settings["gmail_user"], settings["gmail_app_password"],
                   settings["email"], subject, html)
        add_log(user_id, f"✓ Email sent! «{chosen['title']}» (topic: {topic})", "success")

        advance_topic(user_id, settings)
        next_topic = get_active_topic(load_settings(user_id))
        if next_topic and next_topic != topic:
            add_log(user_id, f"Tomorrow's topic will be: «{next_topic}»", "info")

    except Exception as e:
        add_log(user_id, f"Job failed: {e}", "error")


# ── Scheduler ─────────────────────────────────────────────────────────────────

def scheduler_loop() -> None:
    """Every minute, check if any user's send_time matches now and run their job."""
    last_run: dict[int, datetime.date] = {}
    while True:
        now = datetime.datetime.now()
        current_time = now.strftime("%H:%M")
        current_date = now.date()
        with app.app_context():
            users = User.query.all()
            for user in users:
                settings  = load_settings(user.id)
                send_time = settings.get("send_time", "08:00")
                if send_time == current_time and last_run.get(user.id) != current_date:
                    last_run[user.id] = current_date
                    threading.Thread(target=run_daily_job, args=(user.id,), daemon=True).start()
        time.sleep(60)


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET"])
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return send_file("login.html")


@app.route("/login", methods=["POST"])
def login_post():
    data = request.get_json(force=True) or {}
    user = User.query.filter_by(username=data.get("username", "").strip()).first()
    if user and check_password_hash(user.password, data.get("password", "")):
        login_user(user)
        return jsonify({"status": "ok", "is_admin": user.is_admin})
    return jsonify({"status": "error", "message": "Invalid username or password"}), 401


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login_page"))


@app.route("/api/me", methods=["GET"])
@login_required
def api_me():
    return jsonify({"username": current_user.username, "is_admin": current_user.is_admin})


# ── Admin routes ──────────────────────────────────────────────────────────────

@app.route("/admin")
@login_required
def admin_page():
    if not current_user.is_admin:
        return redirect(url_for("index"))
    return send_file("admin.html")


@app.route("/api/admin/users", methods=["GET"])
@login_required
def api_admin_list_users():
    if not current_user.is_admin:
        return jsonify({"error": "Forbidden"}), 403
    users = User.query.all()
    return jsonify([{"id": u.id, "username": u.username, "is_admin": u.is_admin} for u in users])


@app.route("/api/admin/users", methods=["POST"])
@login_required
def api_admin_create_user():
    if not current_user.is_admin:
        return jsonify({"error": "Forbidden"}), 403
    data     = request.get_json(force=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "Username already exists"}), 400
    user = User(
        username=username,
        password=generate_password_hash(password),
        is_admin=bool(data.get("is_admin", False)),
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({"status": "ok", "id": user.id})


@app.route("/api/admin/users/<int:uid>", methods=["DELETE"])
@login_required
def api_admin_delete_user(uid):
    if not current_user.is_admin:
        return jsonify({"error": "Forbidden"}), 403
    if uid == current_user.id:
        return jsonify({"error": "Cannot delete your own account"}), 400
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "User not found"}), 404
    db.session.delete(user)
    db.session.commit()
    return jsonify({"status": "ok"})


@app.route("/api/admin/users/<int:uid>/password", methods=["POST"])
@login_required
def api_admin_reset_password(uid):
    if not current_user.is_admin:
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(force=True) or {}
    new_password = data.get("password", "").strip()
    if not new_password:
        return jsonify({"error": "Password required"}), 400
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "User not found"}), 404
    user.password = generate_password_hash(new_password)
    db.session.commit()
    return jsonify({"status": "ok"})


# ── App routes ────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return send_file("index.html")


@app.route("/api/settings", methods=["GET"])
@login_required
def api_get_settings():
    return jsonify(load_settings(current_user.id))


@app.route("/api/settings", methods=["POST"])
@login_required
def api_save_settings():
    new = request.get_json(force=True) or {}
    settings = load_settings(current_user.id)
    settings.update(new)
    save_settings(current_user.id, settings)
    return jsonify({"status": "ok"})


@app.route("/api/logs", methods=["GET"])
@login_required
def api_logs():
    limit = int(request.args.get("limit", 50))
    return jsonify(load_logs(current_user.id)[:limit])


@app.route("/api/logs", methods=["DELETE"])
@login_required
def api_clear_logs():
    with open(logs_path(current_user.id), "w") as f:
        json.dump([], f)
    return jsonify({"status": "ok"})


@app.route("/api/trigger", methods=["POST"])
@login_required
def api_trigger():
    t = threading.Thread(target=run_daily_job, args=(current_user.id,), daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/api/status", methods=["GET"])
@login_required
def api_status():
    settings   = load_settings(current_user.id)
    required   = ["youtube_api_key", "claude_api_key", "gmail_user", "gmail_app_password", "email"]
    has_topic  = bool(get_active_topic(settings))
    configured = all(settings.get(f) for f in required) and has_topic
    all_topics = [t.strip() for t in settings.get("topics", []) if t.strip()]
    active     = get_active_topic(settings)
    topic_idx  = settings.get("topic_index", 0)
    topic_num  = (topic_idx % len(all_topics)) + 1 if all_topics else 0
    logs = load_logs(current_user.id)
    last = logs[0] if logs else None
    return jsonify({
        "configured":     configured,
        "scheduled_time": settings.get("send_time", "08:00"),
        "active_topic":   active,
        "topic_num":      topic_num,
        "topic_total":    len(all_topics),
        "last_log":       last,
    })


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if not User.query.first():
            admin = User(
                username="admin",
                password=generate_password_hash("admin123"),
                is_admin=True,
            )
            db.session.add(admin)
            db.session.commit()
            print("\n  Default admin created:")
            print("    Username: admin")
            print("    Password: admin123")
            print("  Change this password after first login!\n")

    threading.Thread(target=scheduler_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    print("\n🎬  YouTube Daily Digest (multi-user) is running.")
    print(f"    Open http://localhost:{port} in your browser.\n")
    app.run(host="0.0.0.0", port=port, debug=False)
