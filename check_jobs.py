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


# ----------------------------------------------------------------------
# General helpers
# ----------------------------------------------------------------------

def normalize_url(url):
    """Normalize a job URL for permanent seen-job tracking."""
    if not url:
        return ""

    url = url.strip()

    # Remove URL fragment.
    if "#" in url:
        url = url.split("#", 1)[0]

    return url


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
    """
    Atomically save permanently seen job URLs.
    """
    temp_file = f"{SEEN_FILE}.tmp"

    data = {
        "urls": sorted(seen)
    }

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    os.replace(temp_file, SEEN_FILE)


def clean_text(value):
    if not value:
        return ""

    return " ".join(value.split()).strip()


def absolute_url(base_url, href):
    if not href:
        return ""

    return normalize_url(urljoin(base_url, href))


# ----------------------------------------------------------------------
# LinkedIn
# ----------------------------------------------------------------------

def is_toronto_location(location):
    """
    Determine whether a LinkedIn location belongs to Toronto/GTA.
    """
    location = clean_text(location).lower()

    if not location:
        return False

    for term in TORONTO_LOCATIONS:
        if term in location:
            return True

    return False


def is_remote_canada_location(location):
    """
    Determine whether a LinkedIn location represents a Canada-remote job.
    """
    location = clean_text(location).lower()

    if not location:
        return False

    for term in REMOTE_CANADA_TERMS:
        if term in location:
            return True

    return False


def is_linkedin_location_match(location):
    """
    TEST MODE:

    LinkedIn title filtering is intentionally disabled.

    A job matches if its location is:
      - Toronto/GTA
      OR
      - Remote Canada
    """
    return (
        is_toronto_location(location)
        or is_remote_canada_location(location)
    )


def fetch_linkedin_jobs(search_url):
    """
    Fetch LinkedIn public job search results.

    TEST MODE:
      - No Scrum Master title filter.
      - The search URL itself restricts results to the last hour.
      - We only filter by Toronto/GTA or Remote Canada location.
    """
    print(f"Checking LinkedIn: {search_url}")

    try:
        response = requests.get(
            search_url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        print(f"LinkedIn HTTP status: {response.status_code}")

        response.raise_for_status()

    except requests.RequestException as exc:
        print(f"ERROR: LinkedIn request failed: {exc}")
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

    print(f"LinkedIn matching jobs: {len(jobs)}")

    return jobs


# ----------------------------------------------------------------------
# Indeed
# ----------------------------------------------------------------------

def is_indeed_scrum_master_job(title):
    """
    Keep Indeed behavior unchanged.

    Indeed jobs must contain 'scrum master' in the title.
    """
    return "scrum master" in clean_text(title).lower()


def fetch_indeed_jobs(search_url):
    """
    Fetch Indeed public search results.

    Indeed currently may return HTTP 403 from GitHub Actions.
    That is handled gracefully and results in zero jobs.
    """
    print(f"Checking Indeed: {search_url}")

    try:
        response = requests.get(
            search_url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        print(f"Indeed HTTP status: {response.status_code}")

        response.raise_for_status()

    except requests.RequestException as exc:
        print(f"ERROR: Indeed request failed: {exc}")
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

    print(f"Indeed matching jobs: {len(jobs)}")

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
    Send one job notification to Telegram.

    Returns:
      True  -> Telegram notification succeeded.
      False -> Telegram notification failed.
    """
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")

    if not token or not chat_id:
        print("ERROR: Telegram credentials are not configured.")
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
            print(
                f"Telegram notification sent: "
                f"{job['title']} | {job['url']}"
            )
            return True

        print(
            "ERROR: Telegram notification failed "
            f"with HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )
        return False

    except requests.RequestException as exc:
        print(f"ERROR: Telegram request failed: {exc}")
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
    Send one job notification through Gmail SMTP.

    Gmail is optional.

    Returns:
      True  -> email succeeded.
      False -> email failed.
    """
    email_user = os.getenv("EMAIL_USER")
    email_pass = os.getenv("EMAIL_PASS")
    email_to = os.getenv("EMAIL_TO")

    if not email_user or not email_pass or not email_to:
        print("Gmail is not configured.")
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

        print(
            f"Gmail notification sent: "
            f"{job['title']} | {job['url']}"
        )

        return True

    except Exception as exc:
        print(f"ERROR: Gmail notification failed: {exc}")
        return False


# ----------------------------------------------------------------------
# Notification policy
# ----------------------------------------------------------------------

def notify(job):
    """
    Telegram is the primary notification channel.

    Rules:
      - Telegram success = job is successfully alerted.
      - Gmail is optional and does not determine whether the
        job is marked as seen.
      - If Gmail fails but Telegram succeeds, the job is still
        considered successful and will be marked as seen.
      - If Telegram fails, the job remains unseen.
    """

    telegram_ok = False

    # Telegram is required.
    if telegram_configured():
        telegram_ok = send_telegram(job)
    else:
        print("ERROR: Telegram is not configured.")
        return False

    # Gmail is optional.
    if email_configured():
        email_ok = send_email(job)

        if not email_ok:
            print(
                "WARNING: Gmail notification failed, "
                "but Telegram succeeded. "
                "Job will still be marked as seen."
            )
        else:
            print("Gmail notification succeeded.")

    else:
        print(
            "Gmail is not configured. "
            "Telegram notification is sufficient."
        )

    # Telegram determines whether the job is successfully alerted.
    if telegram_ok:
        print(
            "Telegram notification succeeded. "
            "Job will be marked as seen."
        )
        return True

    print(
        "Telegram notification failed. "
        "Job will NOT be marked as seen."
    )

    return False


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    print("Starting Scrum Master Job Alerts...")
    print()

    seen = load_seen()

    print(f"Previously seen jobs: {len(seen)}")
    print()

    # --------------------------------------------------------------
    # LinkedIn TEST MODE
    #
    # IMPORTANT:
    # No title/role filter yet.
    #
    # These URLs only restrict results to jobs posted in the
    # last hour.
    # --------------------------------------------------------------

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
        jobs = fetch_linkedin_jobs(url)
        linkedin_jobs.extend(jobs)

    # --------------------------------------------------------------
    # Indeed
    #
    # Keep existing Scrum Master searches unchanged.
    # --------------------------------------------------------------

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
        jobs = fetch_indeed_jobs(url)
        indeed_jobs.extend(jobs)

    # --------------------------------------------------------------
    # Combine and deduplicate
    # --------------------------------------------------------------

    all_jobs = linkedin_jobs + indeed_jobs

    unique_jobs = {}

    for job in all_jobs:
        url = normalize_url(job.get("url"))

        if not url:
            continue

        if url not in unique_jobs:
            unique_jobs[url] = job

    jobs = list(unique_jobs.values())

    print()
    print(f"Total unique matching jobs: {len(jobs)}")

    # --------------------------------------------------------------
    # Remove permanently seen jobs
    # --------------------------------------------------------------

    new_jobs = [
        job
        for job in jobs
        if normalize_url(job["url"]) not in seen
    ]

    print(f"New jobs: {len(new_jobs)}")
    print()

    if not new_jobs:
        print("No new jobs found.")
        return

    # --------------------------------------------------------------
    # Notify
    #
    # IMPORTANT:
    # A job is added to seen ONLY when notify() returns True.
    #
    # Since Telegram is the primary channel:
    #
    # Telegram success + Gmail success -> seen
    # Telegram success + Gmail failure  -> seen
    # Telegram failure + Gmail success  -> NOT seen
    # Telegram failure + Gmail failure  -> NOT seen
    # --------------------------------------------------------------

    newly_seen = set()

    for index, job in enumerate(new_jobs, start=1):
        print(
            f"[{index}/{len(new_jobs)}] "
            f"Processing: {job['title']} | "
            f"{job['company'] or 'N/A'} | "
            f"{job['location'] or 'N/A'}"
        )

        try:
            success = notify(job)

        except Exception as exc:
            print(
                f"ERROR: Unexpected notification error: {exc}"
            )
            success = False

        if success:
            newly_seen.add(
                normalize_url(job["url"])
            )

        print()

        # Small delay to avoid hammering notification APIs.
        time.sleep(0.5)

    # --------------------------------------------------------------
    # Persist state
    #
    # Only successfully alerted jobs are added.
    # --------------------------------------------------------------

    if newly_seen:
        seen.update(newly_seen)
        save_seen(seen)

        print(
            f"Marked {len(newly_seen)} job(s) as seen."
        )
        print(
            f"Total permanently seen jobs: {len(seen)}"
        )
    else:
        print(
            "No jobs were successfully notified. "
            "seen.json was not changed."
        )


if __name__ == "__main__":
    main()
