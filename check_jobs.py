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
    "Accept-Language": "en-CA,en;q=0.9,en-US;q=0.8",
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


session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# SEEN STATE
# ============================================================

def normalize_url(url):
    if not url:
        return ""

    return url.strip().split("#", 1)[0]


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()

    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return {
                normalize_url(str(x))
                for x in data
                if x
            }

        if isinstance(data, dict):
            urls = data.get("urls", [])

            if isinstance(urls, list):
                return {
                    normalize_url(str(x))
                    for x in urls
                    if x
                }

    except Exception as exc:
        print(f"WARNING: Could not read {SEEN_FILE}: {exc}")

    return set()


def save_seen(seen):
    values = sorted(
        {
            normalize_url(x)
            for x in seen
            if x
        }
    )

    temp_file = SEEN_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(values, f, indent=2)

    os.replace(temp_file, SEEN_FILE)


# ============================================================
# LOCATION FILTER
# ============================================================

def clean_text(value):
    if not value:
        return ""

    return " ".join(value.split())


def is_toronto_location(location):
    location = clean_text(location).lower()

    return any(
        name in location
        for name in TORONTO_LOCATIONS
    )


def is_remote_canada_location(location):
    location = clean_text(location).lower()

    has_remote = any(
        term in location
        for term in REMOTE_CANADA_TERMS
    )

    if not has_remote:
        return False

    canada_terms = (
        "canada",
        "canadian",
        "nationwide",
        "anywhere in canada",
    )

    return any(
        term in location
        for term in canada_terms
    )


def is_linkedin_location_match(job):
    location = job.get("location", "")

    return (
        is_toronto_location(location)
        or is_remote_canada_location(location)
    )


# ============================================================
# LINKEDIN
# ============================================================

LINKEDIN_SEARCHES = [
    {
        "name": "LinkedIn Toronto",
        "url": (
            "https://www.linkedin.com/jobs/search/"
            "?location=Toronto"
            "&f_TPR=r3600"
        ),
    },
    {
        "name": "LinkedIn Remote Canada",
        "url": (
            "https://www.linkedin.com/jobs/search/"
            "?location=Canada"
            "&f_WT=2"
            "&f_TPR=r3600"
        ),
    },
]


def get_page(url):
    try:
        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        print(
            f"{response.status_code} "
            f"{response.url}"
        )

        return response

    except requests.RequestException as exc:
        print(f"Request failed: {exc}")
        return None


def parse_linkedin(response, search_name):
    jobs = []

    if response is None:
        return jobs

    if response.status_code != 200:
        print(
            f"{search_name}: HTTP "
            f"{response.status_code}"
        )
        return jobs

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    cards = soup.select("div.base-card")

    if not cards:
        cards = soup.select("li")

    for card in cards:
        try:
            title_element = (
                card.select_one(
                    "h3.base-search-card__title"
                )
                or card.select_one("h3")
                or card.select_one("h4")
            )

            if not title_element:
                continue

            title = clean_text(
                title_element.get_text(
                    " ",
                    strip=True,
                )
            )

            company_element = (
                card.select_one(
                    "h4.base-search-card__subtitle"
                )
                or card.select_one("h4")
            )

            company = clean_text(
                company_element.get_text(
                    " ",
                    strip=True,
                )
                if company_element
                else ""
            )

            location_element = (
                card.select_one(
                    "span.job-search-card__location"
                )
                or card.select_one(
                    ".job-search-card__location"
                )
                or card.select_one(
                    ".base-search-card__metadata"
                )
            )

            location = clean_text(
                location_element.get_text(
                    " ",
                    strip=True,
                )
                if location_element
                else ""
            )

            link_element = (
                card.select_one(
                    "a.base-card__full-link"
                )
                or card.select_one(
                    'a[href*="/jobs/view/"]'
                )
            )

            if not link_element:
                continue

            url = link_element.get("href", "")

            if not url:
                continue

            url = urljoin(
                "https://www.linkedin.com",
                url,
            )

            url = normalize_url(url)

            job = {
                "title": title,
                "company": company,
                "location": location,
                "url": url,
                "source": "LinkedIn",
                "search": search_name,
            }

            # TEST CONDITION:
            # No job-title filtering.
            # Any LinkedIn job matching the requested
            # Toronto/GTA or Remote Canada location is accepted.
            if is_linkedin_location_match(job):
                jobs.append(job)

        except Exception:
            continue

    return jobs


def collect_linkedin_jobs():
    jobs = []

    for search in LINKEDIN_SEARCHES:
        print(f"Checking {search['name']}...")

        response = get_page(search["url"])

        found = parse_linkedin(
            response,
            search["name"],
        )

        print(
            f"{search['name']}: "
            f"{len(found)} matching jobs"
        )

        jobs.extend(found)

        time.sleep(2)

    return jobs


# ============================================================
# INDEED
# ============================================================
# KEEPING INDEED IMPLEMENTATION AS-IS.
# ============================================================

INDEED_SEARCHES = [
    {
        "name": "Indeed Toronto",
        "url": (
            "https://ca.indeed.com/jobs"
            "?q=%22Scrum+Master%22"
            "&l=Toronto%2C+ON"
            "&fromage=1"
        ),
    },
    {
        "name": "Indeed Remote Canada",
        "url": (
            "https://ca.indeed.com/jobs"
            "?q=%22Scrum+Master%22"
            "&l=Canada"
            "&sc=0kf%3Aattr%28DSQF7%29%3B"
            "&fromage=1"
        ),
    },
]


def parse_indeed(response, search_name):
    jobs = []

    if response is None:
        return jobs

    if response.status_code != 200:
        print(
            f"{search_name}: Indeed HTTP "
            f"{response.status_code}"
        )
        return jobs

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    selectors = [
        "div.job_seen_beacon",
        "div.jobsearch-SerpJobCard",
        "div.slider_container",
        "td.resultContent",
        "div.cardOutline",
    ]

    cards = []

    for selector in selectors:
        cards.extend(
            soup.select(selector)
        )

    unique_cards = []
    seen_ids = set()

    for card in cards:
        card_id = id(card)

        if card_id not in seen_ids:
            seen_ids.add(card_id)
            unique_cards.append(card)

    for card in unique_cards:
        try:
            title_element = (
                card.select_one("h2.jobTitle a")
                or card.select_one("h2.jobTitle")
                or card.select_one("a.jcs-JobTitle")
                or card.select_one("a[data-jk]")
            )

            if not title_element:
                continue

            title = clean_text(
                title_element.get_text(
                    " ",
                    strip=True,
                )
            )

            company_element = (
                card.select_one(
                    '[data-testid="company-name"]'
                )
                or card.select_one("span.companyName")
                or card.select_one(".companyName")
            )

            company = clean_text(
                company_element.get_text(
                    " ",
                    strip=True,
                )
                if company_element
                else ""
            )

            location_element = (
                card.select_one(
                    '[data-testid="text-location"]'
                )
                or card.select_one("div.companyLocation")
                or card.select_one(".companyLocation")
            )

            location = clean_text(
                location_element.get_text(
                    " ",
                    strip=True,
                )
                if location_element
                else ""
            )

            link_element = (
                card.select_one(
                    'a[href*="/viewjob"]'
                )
                or card.select_one(
                    'a[href*="/rc/clk"]'
                )
                or card.select_one("h2.jobTitle a")
                or card.select_one("a.jcs-JobTitle")
            )

            url = ""

            if link_element:
                url = link_element.get("href", "")

            if not url:
                job_key = (
                    card.get("data-jk")
                    or (
                        title_element.get("data-jk")
                        if title_element
                        else None
                    )
                )

                if job_key:
                    url = (
                        "https://ca.indeed.com/viewjob"
                        f"?jk={job_key}"
                    )

            if not url:
                continue

            url = urljoin(
                "https://ca.indeed.com",
                url,
            )

            url = normalize_url(url)

            # Keep Indeed's existing Scrum Master filtering.
            if "scrum master" not in title.lower():
                continue

            job = {
                "title": title,
                "company": company,
                "location": location,
                "url": url,
                "source": "Indeed",
                "search": search_name,
            }

            jobs.append(job)

        except Exception:
            continue

    return jobs


def collect_indeed_jobs():
    jobs = []

    for search in INDEED_SEARCHES:
        print(f"Checking {search['name']}...")

        response = get_page(search["url"])

        found = parse_indeed(
            response,
            search["name"],
        )

        print(
            f"{search['name']}: "
            f"{len(found)} matching jobs"
        )

        jobs.extend(found)

        time.sleep(2)

    return jobs


# ============================================================
# NOTIFICATIONS
# ============================================================

def send_telegram(job):
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")

    if not token or not chat_id:
        print("Telegram not configured.")
        return True

    message = (
        "New Job Alert\n\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Source: {job.get('source', '')}\n\n"
        f"{job.get('url', '')}"
    )

    url = (
        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    try:
        response = session.post(
            url,
            data={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": False,
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            print(
                f"Telegram failed: "
                f"HTTP {response.status_code}"
            )
            return False

        result = response.json()

        if not result.get("ok"):
            print("Telegram API returned failure.")
            return False

        return True

    except Exception as exc:
        print(
            f"Telegram error: {exc}"
        )
        return False


def send_email(job):
    username = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASS")
    recipient = os.getenv("EMAIL_TO")

    if not username or not password or not recipient:
        return True

    message = EmailMessage()

    message["Subject"] = (
        f"Job Alert: {job.get('title', '')}"
    )

    message["From"] = username
    message["To"] = recipient

    message.set_content(
        "New Job Alert\n\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Source: {job.get('source', '')}\n\n"
        f"URL: {job.get('url', '')}\n"
    )

    try:
        with smtplib.SMTP(
            "smtp.gmail.com",
            587,
            timeout=REQUEST_TIMEOUT,
        ) as server:
            server.starttls()
            server.login(
                username,
                password,
            )
            server.send_message(message)

        return True

    except Exception as exc:
        print(
            f"Email error: {exc}"
        )
        return False


def notify(job):
    results = []

    if (
        os.getenv("TG_BOT_TOKEN")
        and os.getenv("TG_CHAT_ID")
    ):
        results.append(
            send_telegram(job)
        )

    if (
        os.getenv("EMAIL_USER")
        and os.getenv("EMAIL_PASS")
        and os.getenv("EMAIL_TO")
    ):
        results.append(
            send_email(job)
        )

    if not results:
        print(
            "No notification method configured."
        )
        return False

    return all(results)


# ============================================================
# MAIN
# ============================================================

def main():

    seen = load_seen()

    print(
        f"Previously seen jobs: {len(seen)}"
    )

    linkedin_jobs = collect_linkedin_jobs()

    # Keep Indeed implementation active.
    indeed_jobs = collect_indeed_jobs()

    jobs = linkedin_jobs + indeed_jobs

    # Deduplicate by URL.
    unique_jobs = {}

    for job in jobs:
        url = normalize_url(
            job.get("url", "")
        )

        if url and url not in unique_jobs:
            unique_jobs[url] = job

    jobs = list(unique_jobs.values())

    print(
        f"Total unique matching jobs: "
        f"{len(jobs)}"
    )

    new_jobs = []

    for job in jobs:
        url = normalize_url(
            job.get("url", "")
        )

        if url not in seen:
            new_jobs.append(job)

    print(
        f"New jobs: {len(new_jobs)}"
    )

    if not new_jobs:
        print("No new jobs.")
        return

    successfully_notified = []

    for job in new_jobs:

        print(
            f"New: {job.get('title', '')} | "
            f"{job.get('company', '')} | "
            f"{job.get('location', '')}"
        )

        if notify(job):
            successfully_notified.append(job)

            seen.add(
                normalize_url(
                    job.get("url", "")
                )
            )

        else:
            print(
                "Notification failed; "
                "job will remain unseen."
            )

    if successfully_notified:
        save_seen(seen)

    print(
        f"Successfully notified: "
        f"{len(successfully_notified)}"
    )


if __name__ == "__main__":
    main()
