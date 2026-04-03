import requests
from bs4 import BeautifulSoup
import hashlib
import json
import os
import smtplib
from email.mime.text import MIMEText
import html

SEARCH_URL = "https://www.google.com/search?q=(%22QA%22+OR+%22SDET%22+OR+%22QA+Engineer%22+OR+%22QA+Analyst%22+OR+%22Scrum+Master%22)+(%22Toronto%22+OR+%22Remote%22+OR+%22Canada%22)+(site%3Alinkedin.com%2Fjobs+OR+site%3Aindeed.com+OR+site%3Aglassdoor.ca)&tbs=qdr%3Ah"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

# ---------- FETCH ----------
def fetch():
    try:
        r = requests.get(SEARCH_URL, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        jobs = []

        for g in soup.select("div.g"):
            a = g.find("a")
            h3 = g.find("h3")

            if not a or not h3:
                continue

            href = a.get("href", "")
            title = h3.get_text(strip=True)
            snippet = g.get_text(" ", strip=True)

            if "/url?q=" in href:
                link = href.split("/url?q=")[1].split("&")[0]

                if any(x in link for x in ["linkedin.com/jobs", "indeed.com", "glassdoor.ca"]):
                    company, location = extract_company_location(snippet)

                    jobs.append({
                        "title": title,
                        "link": link,
                        "company": company,
                        "location": location
                    })

        return jobs

    except Exception as e:
        print("Fetch error:", e)
        return []

# ---------- EXTRACTION ----------
def extract_company_location(text):
    parts = text.split(" - ")

    company = "Unknown"
    location = "Unknown"

    if len(parts) >= 2:
        company = parts[0][:80]
        location = parts[1][:80]

    return company, location

# ---------- HASH ----------
def hash_title(title):
    return hashlib.md5(title.lower().encode()).hexdigest()

# ---------- STORAGE ----------
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
            <td><a href="{j['link']}">View</a></td>
        </tr>
        """

    return f"""
    <h3>New QA Jobs (Last Hour)</h3>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr>
            <th>Title</th>
            <th>Company</th>
            <th>Location</th>
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
        msg["Subject"] = "QA Job Alerts (Hourly)"
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
        message = "<b>New QA Jobs (Last Hour)</b>\n\n"

        for j in jobs[:10]:
            message += f"• <b>{html.escape(j['title'])}</b>\n"
            message += f"{html.escape(j['company'])} | {html.escape(j['location'])}\n"
            message += f"<a href='{j['link']}'>View Job</a>\n\n"

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
    current = fetch()
    previous = load()

    new_jobs = []
    updated = previous.copy()

    for j in current:
        h = hash_title(j["title"])

        if h not in previous:
            new_jobs.append(j)

        updated[h] = j["link"]

    print(f"Found {len(new_jobs)} new jobs")

    send_email(new_jobs)
    send_telegram(new_jobs)
    save(updated)

if __name__ == "__main__":
    main()
