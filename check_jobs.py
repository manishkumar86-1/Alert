import json
import os
import smtplib
import time
from email.message import EmailMessage
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


SEEN_FILE = "seen.json"
REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-CA,en;q=0.9",
}


# ----------------------------------------------------------------------
# Location and role filters
# ----------------------------------------------------------------------

TORONTO_LOCATIONS = {
    "toronto",
    "mississauga",
    "brampton",
    "markham",
    "richmond hill",
    "vaughan",
    "thornhill",
    "north york",
    "scarborough",
    "etobicoke",
    "oakville",
    "ajax",
    "pickering",
    "whitby",
    "oshawa",
    "aurora",
    "newmarket",
    "king city",
    "milton",
    "gta",
    "greater toronto area",
}


REMOTE_CANADA_TERMS = {
    "remote",
    "work from home",
    "remote canada",
    "canada remote",
    "anywhere in canada",
    "nationwide",
}


ALLOWED_ROLES = {
    "scrum master",
    "quality analyst",
    "software tester",
    "manual tester",
    "software developer",
    "qa engineer",
}


# ----------------------------------------------------------------------
# General helpers
# ----------------------------------------------------------------------

def normalize_url(url):
    """Normalize a job URL for permanent seen-job tracking."""
    if not url:
        return ""

    url = url.strip()

    if "#" in url:
        url = url.split("#", 1)[0]

    return url


def clean_text(value):
    if not value:
        return ""

    return " ".join(value.split()).strip()


def absolute_url(base_url, href):
    if not href:
        return ""

    return normalize_url(urljoin(base_url, href))


def load_seen():
    """
    Load permanently seen job URLs.

    Supports:
      - ["url1", "url2"]
      - {"urls": ["url1", "url2"]}
    """
    if not os.path.exists(SEEN_FILE):
        return set()

    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return {
            normalize_url(url)
            for url in data
            if isinstance(url, str) and url.strip()
        }

    if isinstance(data, dict):
        urls = data.get("urls", [])

        if not isinstance(urls, list):
            raise ValueError(
                f"{SEEN_FILE} is malformed: 'urls' must be a list."
            )

        return {
            normalize_url(url)
            for url in urls
            if isinstance(url, str) and url.strip()
        }

    raise ValueError(
        f"{SEEN_FILE} is malformed: expected a list or object."
    )


def save_seen(seen):
    """Atomically save permanently seen job URLs."""
    temp_file = f"{SEEN_FILE}.tmp"

    data = {
        "urls": sorted(seen)
    }

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    os.replace(temp_file, SEEN_FILE)


# ----------------------------------------------------------------------
# LinkedIn filters
# ----------------------------------------------------------------------

def is_toronto_location(location):
    location = clean_text(location).lower()

    if not location:
        return False

    return any(
        term in location
        for term in TORONTO_LOCATIONS
    )


def is_remote_canada_location(location):
    location = clean_text(location).lower()

    if not location:
        return False

    return any(
        term in location
        for term in REMOTE_CANADA_TERMS
    )


def is_linkedin_location_match(location):
    """
    LinkedIn job must be:
      - Toronto/GTA
      OR
      - Remote Canada
    """
    return (
        is_toronto_location(location)
        or is_remote_canada_location(location)
    )


def is_linkedin_role_match(title):
    """
    LinkedIn job title must contain one of the allowed role phrases.
    """
    title = clean_text(title).lower()

    if not title:
        return False

    return any(
        role in title
        for role in ALLOWED_ROLES
    )


# ----------------------------------------------------------------------
# LinkedIn
# ----------------------------------------------------------------------

def fetch_linkedin_jobs(search_url):
    """
    Fetch LinkedIn public job search results.

    The search URL restricts results to the last hour.
    Python then applies role and location filtering.
    """
    try:
        response = requests.get(
            search_url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

    except requests.RequestException as exc:
        print(f"LinkedIn request failed: {exc}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    cards = soup.select("div.base-card")

    if not cards:
        cards = soup.select("li")

    jobs = []

    for card in cards:
        title_element = (
            card.select_one("h3.base-search-card__title")
            or card.select_one("h3")
            or card.select_one("h4")
        )

        company_element = (
            card.select_one("h4.base-search-card__subtitle")
            or card.select_one("h4")
        )

        location_element = (
            card.select_one("span.job-search-card__location")
            or card.select_one(".job-search-card__location")
            or card.select_one(".base-search-card__metadata")
        )

        link_element = (
            card.select_one("a.base-card__full-link")
            or card.select_one('a[href*="/jobs/view/"]')
        )

        title = clean_text(
            title_element.get_text(" ", strip=True)
            if title_element
            else ""
        )

        company = clean_text(
            company_element.get_text(" ", strip=True)
            if company_element
            else ""
        )

        location = clean_text(
            location_element.get_text(" ", strip=True)
            if location_element
            else ""
        )

        href = (
            link_element.get("href", "")
            if link_element
            else ""
        )

        url = absolute_url(
            "https://www.linkedin.com",
            href,
        )

        if not title or not url:
            continue

        if not is_linkedin_role_match(title):
            continue

        if not is_linkedin_location_match(location):
            continue

        jobs.append(
            {
                "source": "LinkedIn",
                "title": title,
                "company": company,
                "location": location,
                "url": url,
            }
        )

    return jobs


# ----------------------------------------------------------------------
# Indeed
# ----------------------------------------------------------------------

def is_indeed_scrum_master_job(title):
    """Keep Indeed behavior unchanged."""
    return "scrum master" in clean_text(title).lower()


def fetch_indeed_jobs(search_url):
    """
    Fetch Indeed public search results.

    Indeed may return HTTP 403 from GitHub Actions.
    This is handled gracefully.
    """
    try:
        response = requests.get(
            search_url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

    except requests.RequestException as exc:
        print(f"Indeed request failed: {exc}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    jobs = []

    cards = soup.select(
        "div.job_seen_beacon, "
        "div.jobsearch-SerpJobCard, "
        "td.resultContent"
    )

    for card in cards:
        title_element = (
            card.select_one("h2.jobTitle")
            or card.select_one("h2")
            or card.select_one("a.jcs-JobTitle")
        )

        company_element = (
            card.select_one("[data-testid='company-name']")
            or card.select_one(".companyName")
        )

        location_element = (
            card.select_one("[data-testid='text-location']")
            or card.select_one(".companyLocation")
        )

        link_element = (
            card.select_one("a.jcs-JobTitle")
            or card.select_one("h2 a")
        )

        title = clean_text(
            title_element.get_text(" ", strip=True)
            if title_element
            else ""
        )

        company = clean_text(
            company_element.get_text(" ", strip=True)
            if company_element
            else ""
        )

        location = clean_text(
            location_element.get_text(" ", strip=True)
            if location_element
            else ""
        )

        href = (
            link_element.get("href", "")
            if link_element
            else ""
        )

        url = absolute_url(
            "https://ca.indeed.com",
            href,
        )

        if not title or not url:
            continue

        if not is_indeed_scrum_master_job(title):
            continue

        jobs.append(
            {
                "source": "Indeed",
                "title": title,
                "company": company,
                "location": location,
                "url": url,
            }
        )

    return jobs


# ----------------------------------------------------------------------
# Telegram
# ----------------------------------------------------------------------

def telegram_configured():
    return bool(
        os.getenv("TG_BOT_TOKEN")
        and os.getenv("TG_CHAT_ID")
    )


def send_telegram(job):
    """
    Send notification through Telegram.

    Telegram is the primary notification channel.
    """
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")

    if not token or not chat_id:
        print("Telegram is not configured.")
        return False

    message = (
        "Scrum Master Job Alert\n\n"
        f"Source: {job['source']}\n"
        f"Title: {job['title']}\n"
        f"Company: {job['company'] or 'N/A'}\n"
        f"Location: {job['location'] or 'N/A'}\n\n"
        f"{job['url']}"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": False,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if response.ok:
            return True

        print(
            f"Telegram notification failed: "
            f"HTTP {response.status_code}"
        )
        return False

    except requests.RequestException as exc:
        print(f"Telegram request failed: {exc}")
        return False


# ----------------------------------------------------------------------
# Gmail
# ----------------------------------------------------------------------

def email_configured():
    return bool(
        os.getenv("EMAIL_USER")
        and os.getenv("EMAIL_PASS")
        and os.getenv("EMAIL_TO")
    )


def send_email(job):
    """
    Send optional Gmail notification.

    Gmail failure does NOT prevent the job from being marked as seen
    when Telegram succeeds.
    """
    email_user = os.getenv("EMAIL_USER")
    email_pass = os.getenv("EMAIL_PASS")
    email_to = os.getenv("EMAIL_TO")

    if not email_user or not email_pass or not email_to:
        return False

    msg = EmailMessage()

    msg["Subject"] = (
        f"Scrum Master Job Alert: {job['title']}"
    )
    msg["From"] = email_user
    msg["To"] = email_to

    body = (
        "Scrum Master Job Alert\n\n"
        f"Source: {job['source']}\n"
        f"Title: {job['title']}\n"
        f"Company: {job['company'] or 'N/A'}\n"
        f"Location: {job['location'] or 'N/A'}\n\n"
        f"Job URL:\n{job['url']}\n"
    )

    msg.set_content(body)

    try:
        with smtplib.SMTP(
            "smtp.gmail.com",
            587,
            timeout=REQUEST_TIMEOUT,
        ) as server:
            server.starttls()
            server.login(email_user, email_pass)
            server.send_message(msg)

        return True

    except Exception as exc:
        print(f"Gmail notification failed: {exc}")
        return False


# ----------------------------------------------------------------------
# Notification policy
# ----------------------------------------------------------------------

def notify(job):
    """
    Telegram is the primary notification channel.

    Rules:
      Telegram success + Gmail success -> seen
      Telegram success + Gmail failure -> seen
      Telegram success + Gmail absent  -> seen
      Telegram failure + Gmail success -> NOT seen
      Telegram failure + Gmail failure -> NOT seen
    """

    if not telegram_configured():
        print("Telegram is not configured.")
        return False

    telegram_ok = send_telegram(job)

    if not telegram_ok:
        return False

    if email_configured():
        if not send_email(job):
            print(
                "Gmail notification failed, "
                "but Telegram succeeded."
            )

    return True


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    seen = load_seen()

    # LinkedIn:
    # Toronto/GTA, last 1 hour
    # Remote Canada, last 1 hour
    #
    # Role filtering is performed locally.
    linkedin_urls = [
        (
            "https://www.linkedin.com/jobs/search/"
            "?location=Toronto&f_TPR=r3600"
        ),
        (
            "https://www.linkedin.com/jobs/search/"
            "?location=Canada&f_WT=2&f_TPR=r3600"
        ),
    ]

    linkedin_jobs = []

    for url in linkedin_urls:
        linkedin_jobs.extend(
            fetch_linkedin_jobs(url)
        )

    # Indeed:
    # Keep existing Scrum Master searches unchanged.
    indeed_urls = [
        (
            "https://ca.indeed.com/jobs"
            "?q=%22Scrum+Master%22"
            "&l=Toronto%2C+ON"
            "&fromage=1"
        ),
        (
            "https://ca.indeed.com/jobs"
            "?q=%22Scrum+Master%22"
            "&l=Canada"
            "&sc=0kf%3Aattr%28DSQF7%29%3B"
            "&fromage=1"
        ),
    ]

    indeed_jobs = []

    for url in indeed_urls:
        indeed_jobs.extend(
            fetch_indeed_jobs(url)
        )

    # Combine and deduplicate by normalized URL.
    all_jobs = linkedin_jobs + indeed_jobs

    unique_jobs = {}

    for job in all_jobs:
        url = normalize_url(job.get("url"))

        if not url:
            continue

        if url not in unique_jobs:
            unique_jobs[url] = job

    jobs = list(unique_jobs.values())

    # Only process jobs that have never been successfully alerted.
    new_jobs = [
        job
        for job in jobs
        if normalize_url(job["url"]) not in seen
    ]

    if not new_jobs:
        print("No new matching jobs.")
        return

    newly_seen = set()

    for job in new_jobs:
        try:
            if notify(job):
                newly_seen.add(
                    normalize_url(job["url"])
                )

        except Exception as exc:
            print(
                f"Notification error for "
                f"{job['title']}: {exc}"
            )

        time.sleep(0.5)

    # Only successfully alerted jobs are permanently recorded.
    if newly_seen:
        seen.update(newly_seen)
        save_seen(seen)

        print(
            f"Marked {len(newly_seen)} job(s) as seen."
        )
    else:
        print(
            "No jobs were successfully notified. "
            "seen.json was not changed."
        )


if __name__ == "__main__":
    main()
