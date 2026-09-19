"""Client Radar web app: dashboard, keywords, scored mentions, digest, settings."""
import os
import smtplib
import threading
from email.message import EmailMessage
from functools import wraps

from flask import Flask, redirect, render_template, request, session, url_for

import monitor
from db import get_db, get_setting, init_db, set_setting, utcnow
from license import TIERS, issue_key, key_fingerprint, validate_key

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")

# Gumroad product permalink -> tier mapping (fill in after creating products)
GUMROAD_PRODUCTS = {
    os.environ.get("GUMROAD_PERMALINK_STARTER", "client-radar-starter"): "starter",
    os.environ.get("GUMROAD_PERMALINK_GROWTH", "client-radar-growth"): "growth",
    os.environ.get("GUMROAD_PERMALINK_SCALE", "client-radar-scale"): "scale",
}


def licensed():
    return bool(get_setting("license_tier", ""))


@app.before_request
def require_license():
    open_paths = {"/activate", "/webhook/gumroad", "/api/health"}
    if request.path in open_paths or request.path.startswith("/static"):
        return None
    if not licensed():
        return redirect(url_for("activate"))
    return None


def tier_info():
    tier = get_setting("license_tier", "starter")
    return tier, TIERS.get(tier, TIERS["starter"])


# --- auth / licensing ---------------------------------------------------------
@app.route("/activate", methods=["GET", "POST"])
def activate():
    error = ""
    if request.method == "POST":
        key = request.form.get("key", "")
        tier = validate_key(key)
        if tier:
            fp = key_fingerprint(key)
            conn = get_db()
            conn.execute(
                "INSERT OR IGNORE INTO licenses(key_hash, tier, created_at, active) "
                "VALUES(?,?,?,1)",
                (fp, tier, utcnow()),
            )
            conn.commit()
            conn.close()
            set_setting("license_tier", tier)
            return redirect(url_for("dashboard"))
        error = "Invalid license key. Check the key from your Gumroad receipt email."
    return render_template("activate.html", error=error)


@app.route("/api/health")
def health():
    return {"ok": True}


# --- dashboard -----------------------------------------------------------------
@app.route("/")
def dashboard():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) c FROM mentions").fetchone()["c"]
    high = conn.execute(
        "SELECT COUNT(*) c FROM mentions WHERE score >= 7 AND fetched_at > datetime('now','-7 days')"
    ).fetchone()["c"]
    avg = conn.execute("SELECT AVG(score) a FROM mentions").fetchone()["a"] or 0
    recent = conn.execute(
        "SELECT m.*, k.text AS keyword FROM mentions m LEFT JOIN keywords k "
        "ON k.id = m.keyword_id ORDER BY m.id DESC LIMIT 15"
    ).fetchall()
    kw_count = conn.execute("SELECT COUNT(*) c FROM keywords").fetchone()["c"]
    conn.close()
    tier, info = tier_info()
    return render_template(
        "dashboard.html", total=total, high=high, avg=round(avg, 1),
        recent=recent, tier=tier, info=info, kw_count=kw_count,
    )


# --- keywords ------------------------------------------------------------------
@app.route("/keywords", methods=["GET", "POST"])
def keywords():
    tier, info = tier_info()
    error = ""
    conn = get_db()
    if request.method == "POST":
        text = request.form.get("text", "").strip().lower()
        count = conn.execute("SELECT COUNT(*) c FROM keywords").fetchone()["c"]
        if not text:
            error = "Enter a keyword."
        elif count >= info["keywords"]:
            error = (f"Your {tier} plan allows {info['keywords']} keyword(s). "
                     "Upgrade your tier for more.")
        else:
            try:
                conn.execute(
                    "INSERT INTO keywords(text, created_at) VALUES(?, ?)",
                    (text, utcnow()),
                )
                conn.commit()
            except Exception:
                error = "That keyword is already tracked."
    kws = conn.execute("SELECT * FROM keywords ORDER BY id").fetchall()
    conn.close()
    return render_template("keywords.html", kws=kws, tier=tier, info=info, error=error)


@app.route("/keywords/delete/<int:kid>", methods=["POST"])
def keyword_delete(kid):
    conn = get_db()
    conn.execute("DELETE FROM keywords WHERE id=?", (kid,))
    conn.commit()
    conn.close()
    return redirect(url_for("keywords"))


# --- mentions feed ---------------------------------------------------------------
@app.route("/mentions")
def mentions():
    q = request.args.get("q", "").strip()
    min_score = int(request.args.get("min_score", 0) or 0)
    conn = get_db()
    sql = ("SELECT m.*, k.text AS keyword FROM mentions m "
           "LEFT JOIN keywords k ON k.id = m.keyword_id WHERE m.score >= ?")
    params = [min_score]
    if q:
        sql += " AND (m.title LIKE ? OR m.body LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY m.score DESC, m.id DESC LIMIT 200"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return render_template("mentions.html", rows=rows, q=q, min_score=min_score)


# --- digest ----------------------------------------------------------------------
def digest_rows(limit=10):
    conn = get_db()
    rows = conn.execute(
        "SELECT m.*, k.text AS keyword FROM mentions m "
        "LEFT JOIN keywords k ON k.id = m.keyword_id "
        "WHERE m.fetched_at > datetime('now','-1 day') AND m.score >= 5 "
        "ORDER BY m.score DESC LIMIT ?", (limit,),
    ).fetchall()
    conn.close()
    return rows


@app.route("/digest", methods=["GET", "POST"])
def digest():
    msg = ""
    if request.method == "POST":
        ok, info_msg = send_digest_email()
        msg = info_msg
    rows = digest_rows(20)
    return render_template("digest.html", rows=rows, msg=msg)


def send_digest_email():
    to_addr = get_setting("digest_email", "").strip()
    host = os.environ.get("SMTP_HOST", "")
    if not to_addr or not host:
        return False, "Digest email not configured (set digest_email + SMTP_HOST)."
    rows = digest_rows(10)
    body = "Top buying-intent mentions in the last 24h:\n\n"
    for r in rows:
        body += f"[{r['score']}/10] {r['title']}\n{r['url']}\n{r['rationale']}\n\n"
    if not rows:
        body += "(No scored mentions in the last 24h.)\n"
    m = EmailMessage()
    m["Subject"] = "Client Radar daily digest"
    m["From"] = os.environ.get("SMTP_FROM", "client-radar@localhost")
    m["To"] = to_addr
    m.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
            s.send_message(m)
        return True, f"Digest sent to {to_addr}."
    except Exception as e:
        return False, f"Email failed: {e}"


# --- settings ----------------------------------------------------------------------
@app.route("/settings", methods=["GET", "POST"])
def settings():
    msg = ""
    if request.method == "POST":
        for k in ("llm_endpoint", "llm_model", "poll_interval", "digest_email"):
            set_setting(k, request.form.get(k, "").strip())
        # Only overwrite the API key when a new value is actually typed.
        new_key = request.form.get("llm_api_key", "").strip()
        if new_key and new_key != "••••••••":
            set_setting("llm_api_key", new_key)
        msg = "Settings saved. (API key is stored server-side and never displayed.)"
    masked = "••••••••" if get_setting("llm_api_key") else ""
    vals = {k: get_setting(k) for k in
            ("llm_endpoint", "llm_model", "poll_interval", "digest_email")}
    vals["llm_api_key"] = masked
    if not vals["poll_interval"]:
        vals["poll_interval"] = "30"
    if not vals["llm_model"]:
        vals["llm_model"] = "gpt-4o-mini"
    tier, info = tier_info()
    return render_template("settings.html", vals=vals, msg=msg, tier=tier, info=info)


# --- manual poll ---------------------------------------------------------------------
@app.route("/api/poll", methods=["POST"])
def api_poll():
    t = threading.Thread(target=monitor.poll_once, daemon=True)
    t.start()
    return {"ok": True, "status": "poll started"}


# --- Gumroad webhook (sale -> license key) --------------------------------------------
@app.route("/webhook/gumroad", methods=["POST"])
def gumroad_webhook():
    """Gumroad posts sale webhooks here. Map product permalink -> tier,
    issue a license key, and email it if SMTP is configured.

    Real wiring (documented in README): Gumroad product Settings -> Advanced ->
    add webhook URL https://YOUR-HOST/webhook/gumroad. Gumroad does not sign
    webhooks; mitigate by keeping this URL unlisted and checking the
    X-Gumroad-* headers / sale payload fields below.
    """
    data = request.form.to_dict() or (request.get_json(silent=True) or {})
    permalink = data.get("product_permalink") or data.get("short_product_id", "")
    email = data.get("email", "")
    tier = None
    for p, t in GUMROAD_PRODUCTS.items():
        if p and p in str(permalink):
            tier = t
            break
    if not tier:
        return {"ok": False, "error": "unknown product"}, 400
    key = issue_key(tier)
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO licenses(key_hash, tier, email, created_at, active) "
        "VALUES(?,?,?,?,1)",
        (key_fingerprint(key), tier, email, utcnow()),
    )
    conn.commit()
    conn.close()
    # Email the key if SMTP is configured; otherwise the seller delivers it
    # via Gumroad's receipt workflow (see README).
    host = os.environ.get("SMTP_HOST", "")
    emailed = False
    if host and email:
        try:
            m = EmailMessage()
            m["Subject"] = "Your Client Radar license key"
            m["From"] = os.environ.get("SMTP_FROM", "client-radar@localhost")
            m["To"] = email
            m.set_content(
                f"Thanks for purchasing Client Radar ({tier})!\n\n"
                f"Your license key:\n\n{key}\n\n"
                f"Activate it at https://YOUR-HOST/activate\n"
            )
            with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
                s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
                s.send_message(m)
            emailed = True
        except Exception as e:
            print(f"[webhook] key email failed: {e}")
    return {"ok": True, "tier": tier, "emailed": emailed,
            **({"key": key} if not emailed else {})}


def run():
    init_db()
    interval = int(get_setting("poll_interval", "30") or 30)
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(monitor.poll_once, "interval", minutes=interval,
                  max_instances=1, coalesce=True)
    sched.start()
    print(f"[app] scheduler started: poll every {interval} min")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))


if __name__ == "__main__":
    run()
