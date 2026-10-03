import json
import os
import smtplib
import html
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from email.mime.text import MIMEText


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

SEEN_FILE = "seen.json"


# ============================================================
# JOB FILTER
# ============================================================

def is_relevant(job):
    """
    Only Scrum Master roles are relevant.
    QA, SDET, Tester, etc. are intentionally NOT included.
    """

    title = job.get("title", "").strip().lower()
    location = job.get("location", "").strip().lower()

    if not title:
        return False

    # ONLY Scrum Master roles
    if "scrum master" not in title:
        return False

    # Toronto / GTA OR Remote Canada
    toronto_locations = [
        "toronto",
        "north york",
        "scarborough",
        "etobicoke",
        "markham",
        "richmond hill",
        "vaughan",
        "thornhill",
        "mississauga",
        "brampton",
        "oakville",
        "ajax",
        "pickering",
        "whitby",
        "oshawa",
        "gta",
    ]

    remote_locations = [
        "remote",
        "canada",
    ]

    location_match = (
        any(value in location for value in toronto_locations)
        or (
            "remote" in location
            and "canada" in location
        )
    )

    if not location_match:
        return False

    # Avoid obvious non-job results
    excluded = [
        "course",
        "bootcamp",
        "training",
        "certificate",
        "certification course",
    ]

    if any(value in title for value in excluded):
        return False

    return True


# ============================================================
# HELPERS
# ============================================================

def normalize_url(url, base_url):
    """Return a clean absolute URL."""
    if not url:
        return ""

    url = url.strip()

    absolute = urljoin(base_url, url)

    # Remove fragment
    parsed = urlparse(absolute)
    return parsed._replace(fragment="").geturl()


def job_id(job):
    """
    Use the actual posting URL as the stable identifier.

    This is much safer than using:
        title + company + location

    because a company can have multiple Scrum Master
    openings with the same title/location.
    """

    link = job.get("link", "").strip()

    if link:
        return link

    # Fallback only if a source doesn't provide a URL.
    title = job.get("title", "").strip().lower()
    company = job.get("company", "").strip().lower()
    location = job.get("location", "").strip().lower()

    return f"{title}|{company}|{location}"


# ============================================================
# INDEED
# ============================================================

def fetch_indeed():
    """
    Search Indeed for Scrum Master jobs in Toronto.

    The search itself is restricted to Scrum Master.
    """

    url = (
        "https://ca.indeed.com/jobs"
        "?q=%22Scrum+Master%22"
        "&l=Toronto%2C+ON"
        "&fromage=1"
    )

    jobs = []

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
        )

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        cards = soup.select("a.tapItem")

        print(f"Indeed cards found: {len(cards)}")

        for card in cards:
            try:
                title_el = card.select_one("h2 span")
                company_el = card.select_one(".companyName")
                location_el = card.select_one(".companyLocation")

                if not title_el:
                    continue

                raw_link = card.get("href")

                if not raw_link:
                    continue

                title = title_el.get_text(" ", strip=True)
                company = (
                    company_el.get_text(" ", strip=True)
                    if company_el
                    else "Unknown"
                )
                location = (
                    location_el.get_text(" ", strip=True)
                    if location_el
                    else "Unknown"
                )

                link = normalize_url(
                    raw_link,
                    "https://ca.indeed.com",
                )

                job = {
                    "title": title,
                    "company": company,
                    "location": location,
                    "link": link,
                    "source": "Indeed",
                }

                if is_relevant(job):
                    jobs.append(job)

            except Exception as error:
                print(f"Indeed card error: {error}")
                continue

    except requests.RequestException as error:
        print(f"Indeed request error: {error}")

    except Exception as error:
        print(f"Indeed error: {error}")

    return jobs


# ============================================================
# LINKEDIN
# ============================================================

def fetch_linkedin():
    """
    Search LinkedIn for Scrum Master jobs in Toronto.

    f_TPR=r3600 = jobs posted in approximately the last hour.
    """

    url = (
        "https://www.linkedin.com/jobs/search/"
        "?keywords=Scrum%20Master"
        "&location=Toronto"
        "&f_TPR=r3600"
    )

    jobs = []

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
        )

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        cards = soup.select("li")

        print(f"LinkedIn cards found: {len(cards)}")

        for card in cards:
            try:
                title_el = card.select_one("h3")
                company_el = card.select_one("h4")
                location_el = card.select_one(
                    ".job-search-card__location"
                )
                link_el = card.select_one(
                    "a.base-card__full-link"
                )

                # Fallback if LinkedIn changes the link class.
                if not link_el:
                    link_el = card.select_one("a")

                if not title_el or not link_el:
                    continue

                raw_link = link_el.get("href")

                if not raw_link:
                    continue

                title = title_el.get_text(" ", strip=True)

                company = (
                    company_el.get_text(" ", strip=True)
                    if company_el
                    else "Unknown"
                )

                location = (
                    location_el.get_text(" ", strip=True)
                    if location_el
                    else "Unknown"
                )

                link = normalize_url(
                    raw_link,
                    "https://www.linkedin.com",
                )

                job = {
                    "title": title,
                    "company": company,
                    "location": location,
                    "link": link,
                    "source": "LinkedIn",
                }

                if is_relevant(job):
                    jobs.append(job)

            except Exception as error:
                print(f"LinkedIn card error: {error}")
                continue

    except requests.RequestException as error:
        print(f"LinkedIn request error: {error}")

    except Exception as error:
        print(f"LinkedIn error: {error}")

    return jobs


# ============================================================
# COMBINE SOURCES
# ============================================================

def fetch_all():
    indeed_jobs = fetch_indeed()
    linkedin_jobs = fetch_linkedin()

    jobs = indeed_jobs + linkedin_jobs

    # Remove duplicate URLs from the same run.
    unique = {}

    for job in jobs:
        unique[job_id(job)] = job

    jobs = list(unique.values())

    print(f"Indeed Scrum Master jobs: {len(indeed_jobs)}")
    print(f"LinkedIn Scrum Master jobs: {len(linkedin_jobs)}")
    print(f"Unique relevant jobs: {len(jobs)}")

    return jobs


# ============================================================
# STATE
# ============================================================

def load():
    if not os.path.exists(SEEN_FILE):
        return {}

    try:
        with open(
            SEEN_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        if isinstance(data, dict):
            return data

        return {}

    except Exception as error:
        print(f"Could not load {SEEN_FILE}: {error}")
        return {}


def save(data):
    """
    Write state atomically.

    This prevents a partially-written JSON file if the
    process is interrupted.
    """

    temporary_file = f"{SEEN_FILE}.tmp"

    with open(
        temporary_file,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
        )

    os.replace(
        temporary_file,
        SEEN_FILE,
    )


# ============================================================
# EMAIL
# ============================================================

def build_email(jobs):
    rows = ""

    for job in jobs:
        safe_title = html.escape(job["title"])
        safe_company = html.escape(job["company"])
        safe_location = html.escape(job["location"])
        safe_source = html.escape(job["source"])
        safe_link = html.escape(
            job["link"],
            quote=True,
        )

        rows += f"""
        <tr>
            <td>{safe_title}</td>
            <td>{safe_company}</td>
            <td>{safe_location}</td>
            <td>{safe_source}</td>
            <td>
                <a href="{safe_link}">View Job</a>
            </td>
        </tr>
        """

    return f"""
    <html>
    <body>
        <h3>New Scrum Master Job Alerts</h3>

        <table
            border="1"
            cellpadding="6"
            cellspacing="0"
        >
            <tr>
                <th>Title</th>
                <th>Company</th>
                <th>Location</th>
                <th>Source</th>
                <th>Link</th>
            </tr>

            {rows}

        </table>
    </body>
    </html>
    """


def send_email(jobs):
    if not jobs:
        return True

    email_user = os.environ.get("EMAIL_USER")
    email_pass = os.environ.get("EMAIL_PASS")
    email_to = os.environ.get("EMAIL_TO")

    if not email_user or not email_pass or not email_to:
        print("Email credentials are not configured.")
        return False

    try:
        message = MIMEText(
            build_email(jobs),
            "html",
        )

        message["Subject"] = "Scrum Master Job Alerts"
        message["From"] = email_user
        message["To"] = email_to

        with smtplib.SMTP_SSL(
            "smtp.gmail.com",
            465,
            timeout=20,
        ) as server:

            server.login(
                email_user,
                email_pass,
            )

            server.send_message(message)

        print("Email sent successfully.")
        return True

    except Exception as error:
        print(f"Email error: {error}")
        return False


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(jobs):
    if not jobs:
        return True

    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")

    if not token or not chat_id:
        print("Telegram credentials are not configured.")
        return False

    try:
        message = "<b>New Scrum Master Job Alerts</b>\n\n"

        for job in jobs[:10]:
            safe_title = html.escape(job["title"])
            safe_company = html.escape(job["company"])
            safe_location = html.escape(job["location"])
            safe_source = html.escape(job["source"])
            safe_link = html.escape(
                job["link"],
                quote=True,
            )

            message += (
                f"• <b>{safe_title}</b>\n"
                f"{safe_company} | {safe_location}\n"
                f"[{safe_source}] "
                f"<a href=\"{safe_link}\">View</a>\n\n"
            )

        url = (
            f"https://api.telegram.org/"
            f"bot{token}/sendMessage"
        )

        response = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram API error: {result}"
            )

        print("Telegram message sent successfully.")
        return True

    except Exception as error:
        print(f"Telegram error: {error}")
        return False


# ============================================================
# MAIN
# ============================================================

def main():
    current_jobs = fetch_all()
    previous = load()

    new_jobs = []

    for job in current_jobs:
        key = job_id(job)

        if key not in previous:
            new_jobs.append(job)

    print(f"New Scrum Master jobs: {len(new_jobs)}")

    if not new_jobs:
        print("No new jobs.")
        return

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Do NOT mark jobs as seen until notification succeeds.
    #
    # This means if Telegram/email fails, the same job will
    # be retried on the next run instead of being permanently
    # lost.
    # --------------------------------------------------------

    email_configured = all(
        os.environ.get(name)
        for name in [
            "EMAIL_USER",
            "EMAIL_PASS",
            "EMAIL_TO",
        ]
    )

    telegram_configured = all(
        os.environ.get(name)
        for name in [
            "TG_BOT_TOKEN",
            "TG_CHAT_ID",
        ]
    )

    notification_results = []

    if email_configured:
        notification_results.append(
            send_email(new_jobs)
        )

    if telegram_configured:
        notification_results.append(
            send_telegram(new_jobs)
        )

    # If no notification channel is configured, fail.
    if not notification_results:
        raise RuntimeError(
            "No notification channel is configured."
        )

    # Every configured notification channel must succeed.
    if not all(notification_results):
        raise RuntimeError(
            "One or more notifications failed. "
            "Jobs will NOT be marked as seen."
        )

    # Only now mark jobs as seen.
    updated = previous.copy()

    for job in new_jobs:
        updated[job_id(job)] = {
            "title": job["title"],
            "company": job["company"],
            "location": job["location"],
            "link": job["link"],
            "source": job["source"],
        }

    save(updated)

    print(
        f"Successfully saved {len(new_jobs)} new jobs."
    )


if __name__ == "__main__":
    main()
