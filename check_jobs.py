import requests
from bs4 import BeautifulSoup
import hashlib
import json
import os
import smtplib
from email.mime.text import MIMEText
import html

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Language": "en-US,en;q=0.9"
}

# ---------- INDEED ----------
def fetch_indeed():
    url = "https://ca.indeed.com/jobs?q=QA+SDET+Automation+Tester&l=Toronto%2C+ON&fromage=1"
    jobs = []

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        for card in soup.select("a.tapItem"):
            title_el = card.select_one("h2 span")
            company_el = card.select_one(".companyName")
            location_el = card.select_one(".companyLocation")

            title = title_el.text.strip() if title_el else "Unknown"
            company = company_el.text.strip() if company_el else "Unknown"
            location = location_el.text.strip() if location_el else "Unknown"

            link = "https://ca.indeed.com" + card.get("href")

            jobs.append({
                "title": title,
                "company": company,
                "location": location,
                "link": link,
                "source": "Indeed"
            })

    except Exception as e:
        print("Indeed error:", e)

    return jobs

# ---------- LINKEDIN ----------
def fetch_linkedin():
    url = "https://www.linkedin.com/jobs/search/?keywords=QA&location=Toronto&f_TPR=r3600"
    jobs = []

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        for card in soup.select("li"):
            title_el = card.select_one("h3")
            company_el = card.select_one("h4")
            location_el = card.select_one(".job-search-card__location")
            link_el = card.select_one("a")

            if not title_el or not link_el:
                continue

            jobs.append({
                "title": title_el.text.strip(),
                "company": company_el.text.strip() if company_el else "Unknown",
                "location": location_el.text.strip() if location_el else "Unknown",
                "link": link_el.get("href"),
                "source": "LinkedIn"
            })

    except Exception as e:
        print("LinkedIn error:", e)

    return jobs

# ---------- COMBINE ----------
def fetch_all():
    jobs = []
    jobs += fetch_indeed()
    jobs += fetch_linkedin()
    print(f"Fetched total jobs: {len(jobs)}")
    return jobs

# ---------- DEDUP ----------
def hash_job(job):
    key = (job["title"] + job["company"] + job["location"]).lower()
    return hashlib.md5(key.encode()).hexdigest()

def load():
    if not os.path.exists("seen.json"):
        return {}
    with open("seen.json", "r") as f:
        return json.load(f)

def save(data):
    with open("seen.json", "w") as f:
        json.dump(data, f)

# ---------- EMAIL ----------
def build_email(jobs):
    rows = ""
    for j in jobs:
        rows += f"""
        <tr>
            <td>{html.escape(j['title'])}</td>
            <td>{html.escape(j['company'])}</td>
            <td>{html.escape(j['location'])}</td>
            <td>{j['source']}</td>
            <td><a href="{j['link']}">View</a></td>
        </tr>
        """

    return f"""
    <h3>New Jobs (Multi-Source)</h3>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr>
            <th>Title</th>
            <th>Company</th>
            <th>Location</th>
            <th>Source</th>
            <th>Link</th>
        </tr>
        {rows}
    </table>
    """

def send_email(jobs):
    if not jobs:
        return

    try:
        msg = MIMEText(build_email(jobs), "html")
        msg["Subject"] = "QA Job Alerts"
        msg["From"] = os.environ.get("EMAIL_USER")
        msg["To"] = os.environ.get("EMAIL_TO")

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(os.environ.get("EMAIL_USER"), os.environ.get("EMAIL_PASS"))
            s.send_message(msg)

    except Exception as e:
        print("Email error:", e)

# ---------- TELEGRAM ----------
def send_telegram(jobs):
    if not jobs:
        return

    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")

    if not token or not chat_id:
        return

    try:
        message = "<b>New Jobs</b>\n\n"

        for j in jobs[:10]:
            message += f"• <b>{html.escape(j['title'])}</b>\n"
            message += f"{html.escape(j['company'])} | {html.escape(j['location'])}\n"
            message += f"[{j['source']}] <a href='{j['link']}'>View</a>\n\n"

        url = f"https://api.telegram.org/bot{token}/sendMessage"

        requests.post(url, data={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=10)

    except Exception as e:
        print("Telegram error:", e)

# ---------- MAIN ----------
def main():
    current = fetch_all()
    previous = load()

    new_jobs = []
    updated = previous.copy()

    for j in current:
        h = hash_job(j)

        if h not in previous:
            new_jobs.append(j)

        updated[h] = j["link"]

    print(f"New jobs found: {len(new_jobs)}")

    send_email(new_jobs)
    send_telegram(new_jobs)
    save(updated)

if __name__ == "__main__":
    main()
