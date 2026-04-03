@"
import requests
from bs4 import BeautifulSoup
import hashlib
import json
import os
import smtplib
from email.mime.text import MIMEText

SEARCH_URL = "https://www.google.com/search?q=(%22QA%22+OR+%22SDET%22+OR+%22QA+Engineer%22+OR+%22QA+Analyst%22+OR+%22Scrum+Master%22)+(%22Toronto%22+OR+%22Remote%22+OR+%22Canada%22)+(site%3Alinkedin.com%2Fjobs+OR+site%3Aindeed.com+OR+site%3Aglassdoor.ca)&tbs=qdr%3Ah"

HEADERS = {"User-Agent": "Mozilla/5.0"}

def fetch():
    r = requests.get(SEARCH_URL, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")

    jobs = []
    for g in soup.select("div.g"):
        a = g.find("a")
        h3 = g.find("h3")
        if not a or not h3:
            continue

        href = a.get("href", "")
        title = h3.get_text(strip=True)

        if "/url?q=" in href:
            link = href.split("/url?q=")[1].split("&")[0]
            if any(x in link for x in ["linkedin.com/jobs", "indeed.com", "glassdoor.ca"]):
                jobs.append({"title": title, "link": link})

    return jobs

def hash_title(t):
    return hashlib.md5(t.lower().encode()).hexdigest()

def load():
    if not os.path.exists("seen.json"):
        return {}
    return json.load(open("seen.json"))

def save(data):
    json.dump(data, open("seen.json","w"))

def email(jobs):
    if not jobs:
        return

    rows = "".join([f"<tr><td>{j['title']}</td><td><a href='{j['link']}'>View</a></td></tr>" for j in jobs])

    html = f"<h3>New Jobs</h3><table border=1>{rows}</table>"

    msg = MIMEText(html, "html")
    msg["Subject"] = "QA Jobs (Hourly)"
    msg["From"] = os.environ["EMAIL_USER"]
    msg["To"] = os.environ["EMAIL_TO"]

    with smtplib.SMTP_SSL("smtp.gmail.com",465) as s:
        s.login(os.environ["EMAIL_USER"], os.environ["EMAIL_PASS"])
        s.send_message(msg)

def main():
    current = fetch()
    prev = load()

    new = []
    updated = prev.copy()

    for j in current:
        h = hash_title(j["title"])
        if h not in prev:
            new.append(j)
        updated[h] = j["link"]

    email(new)
    save(updated)

if __name__ == "__main__":
    main()
"@ | Out-File -Encoding utf8 check_jobs.py