import html
import json
import os
import smtplib
from email.mime.text import MIMEText
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


SEEN_FILE = "seen.json"
REQUEST_TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ============================================================
# LOCATION CONFIGURATION
# ============================================================

TORONTO_LOCATIONS = [
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
    "aurora",
    "newmarket",
    "king city",
    "milton",
    "gta",
    "greater toronto area",
]


REMOTE_CANADA_TERMS = [
    "remote",
    "work from home",
    "remote canada",
    "canada remote",
    "anywhere in canada",
    "nationwide",
]


# ============================================================
# LINKEDIN LOCATION FILTER
# ============================================================

def is_linkedin_location_match(location):
    """
    Current TEST MODE.

    LinkedIn title filtering is intentionally disabled.

    We are testing whether LinkedIn can reliably return:
      1. Toronto/GTA jobs
      2. Remote Canada jobs
      3. Posted within the last hour

    Title filtering will be restored after acquisition,
    notification, and persistence are verified.
    """

    location = (
        location
        or ""
    ).strip().lower()

    if not location:
        return False

    toronto_match = any(
        location_name in location
        for location_name in TORONTO_LOCATIONS
    )

    remote_canada_match = (
        any(
            term in location
            for term in REMOTE_CANADA_TERMS
        )
        and (
            "canada" in location
            or "nationwide" in location
            or "anywhere in canada" in location
            or location.strip() == "remote"
        )
    )

    return (
        toronto_match
        or remote_canada_match
    )


# ============================================================
# INDEED FILTER
# ============================================================

def is_indeed_relevant(job):
    """
    Indeed remains restricted to Scrum Master roles.
    """

    title = (
        job.get("title", "")
        .strip()
        .lower()
    )

    location = (
        job.get("location", "")
        .strip()
        .lower()
    )

    if "scrum master" not in title:
        return False

    excluded_title_words = [
        "course",
        "bootcamp",
        "training",
        "certificate",
        "certification course",
    ]

    if any(
        word in title
        for word in excluded_title_words
    ):
        return False

    toronto_match = any(
        location_name in location
        for location_name in TORONTO_LOCATIONS
    )

    remote_canada_match = (
        "remote" in location
        and (
            "canada" in location
            or "nationwide" in location
            or "anywhere in canada" in location
        )
    )

    return (
        toronto_match
        or remote_canada_match
    )


# ============================================================
# URL NORMALIZATION
# ============================================================

def normalize_url(url, base_url):
    """
    Convert a relative URL to an absolute URL
    and remove URL fragments.
    """

    if not url:
        return ""

    absolute_url = urljoin(
        base_url,
        url.strip(),
    )

    parsed = urlparse(
        absolute_url
    )

    return parsed._replace(
        fragment=""
    ).geturl()


# ============================================================
# JOB ID
# ============================================================

def job_id(job):
    """
    Use the posting URL as the permanent identifier.

    If a source does not provide a URL, fall back to:
        title + company + location
    """

    link = (
        job.get("link", "")
        .strip()
    )

    if link:
        return link

    title = (
        job.get("title", "")
        .strip()
        .lower()
    )

    company = (
        job.get("company", "")
        .strip()
        .lower()
    )

    location = (
        job.get("location", "")
        .strip()
        .lower()
    )

    return (
        f"{title}|"
        f"{company}|"
        f"{location}"
    )


# ============================================================
# INDEED
# ============================================================

def fetch_indeed():
    """
    Search Indeed for:

    1. Scrum Master + Toronto
       Last 1 day

    2. Scrum Master + Remote Canada
       Last 1 day
    """

    search_urls = [
        (
            "Toronto",
            (
                "https://ca.indeed.com/jobs"
                "?q=%22Scrum+Master%22"
                "&l=Toronto%2C+ON"
                "&fromage=1"
            ),
        ),
        (
            "Remote Canada",
            (
                "https://ca.indeed.com/jobs"
                "?q=%22Scrum+Master%22"
                "&l=Canada"
                "&sc=0kf%3Aattr%28DSQF7%29%3B"
                "&fromage=1"
            ),
        ),
    ]

    jobs = []

    for search_name, url in search_urls:

        print(
            f"Checking Indeed {search_name}..."
        )

        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            print(
                f"Indeed {search_name}: "
                f"HTTP {response.status_code}"
            )

            response.raise_for_status()

            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )

            cards = soup.select(
                "a.tapItem"
            )

            print(
                f"Indeed {search_name}: "
                f"{len(cards)} cards found"
            )

            for card in cards:

                try:
                    title_el = card.select_one(
                        "h2 span"
                    )

                    company_el = card.select_one(
                        ".companyName"
                    )

                    location_el = card.select_one(
                        ".companyLocation"
                    )

                    if not title_el:
                        continue

                    raw_link = card.get(
                        "href"
                    )

                    if not raw_link:
                        continue

                    title = title_el.get_text(
                        " ",
                        strip=True,
                    )

                    company = (
                        company_el.get_text(
                            " ",
                            strip=True,
                        )
                        if company_el
                        else "Unknown"
                    )

                    location = (
                        location_el.get_text(
                            " ",
                            strip=True,
                        )
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

                    if is_indeed_relevant(job):
                        jobs.append(job)

                except Exception as error:
                    print(
                        f"Indeed card error: {error}"
                    )

        except requests.RequestException as error:
            print(
                f"Indeed {search_name} "
                f"request failed: {error}"
            )

        except Exception as error:
            print(
                f"Indeed {search_name} "
                f"error: {error}"
            )

    return jobs


# ============================================================
# LINKEDIN
# ============================================================

def fetch_linkedin():
    """
    TEST MODE.

    LinkedIn searches are intentionally NOT restricted
    by keywords.

    Toronto:
        last 1 hour

    Remote Canada:
        last 1 hour

    Title filtering is intentionally disabled for this test.
    """

    search_urls = [
        (
            "Toronto",
            (
                "https://www.linkedin.com/jobs/search/"
                "?location=Toronto"
                "&f_TPR=r3600"
            ),
        ),
        (
            "Remote Canada",
            (
                "https://www.linkedin.com/jobs/search/"
                "?location=Canada"
                "&f_WT=2"
                "&f_TPR=r3600"
            ),
        ),
    ]

    jobs = []

    for search_name, url in search_urls:

        print(
            f"Checking LinkedIn {search_name}..."
        )

        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            print(
                f"LinkedIn {search_name}: "
                f"HTTP {response.status_code}"
            )

            response.raise_for_status()

            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )

            cards = soup.select(
                "div.base-card"
            )

            if not cards:
                cards = soup.select(
                    "li"
                )

            print(
                f"LinkedIn {search_name}: "
                f"{len(cards)} cards found"
            )

            for card in cards:

                try:
                    title_el = (
                        card.select_one(
                            "h3.base-search-card__title"
                        )
                        or card.select_one(
                            "h3"
                        )
                        or card.select_one(
                            "h4"
                        )
                    )

                    company_el = (
                        card.select_one(
                            "h4.base-search-card__subtitle"
                        )
                        or card.select_one(
                            "h4"
                        )
                    )

                    location_el = (
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

                    link_el = (
                        card.select_one(
                            "a.base-card__full-link"
                        )
                        or card.select_one(
                            'a[href*="/jobs/view/"]'
                        )
                    )

                    if not title_el:
                        continue

                    if not link_el:
                        continue

                    raw_link = link_el.get(
                        "href"
                    )

                    if not raw_link:
                        continue

                    title = title_el.get_text(
                        " ",
                        strip=True,
                    )

                    company = (
                        company_el.get_text(
                            " ",
                            strip=True,
                        )
                        if company_el
                        else "Unknown"
                    )

                    location = (
                        location_el.get_text(
                            " ",
                            strip=True,
                        )
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

                    # TEST MODE:
                    # Only location is filtered.
                    if is_linkedin_location_match(
                        location
                    ):
                        jobs.append(job)

                except Exception as error:
                    print(
                        f"LinkedIn card error: {error}"
                    )

        except requests.RequestException as error:
            print(
                f"LinkedIn {search_name} "
                f"request failed: {error}"
            )

        except Exception as error:
            print(
                f"LinkedIn {search_name} "
                f"error: {error}"
            )

    return jobs


# ============================================================
# FETCH ALL SOURCES
# ============================================================

def fetch_all():
    indeed_jobs = fetch_indeed()
    linkedin_jobs = fetch_linkedin()

    jobs = (
        indeed_jobs
        + linkedin_jobs
    )

    unique_jobs = {}

    for job in jobs:
        unique_jobs[
            job_id(job)
        ] = job

    jobs = list(
        unique_jobs.values()
    )

    print(
        f"Indeed jobs: "
        f"{len(indeed_jobs)}"
    )

    print(
        f"LinkedIn jobs: "
        f"{len(linkedin_jobs)}"
    )

    print(
        f"Total unique jobs: "
        f"{len(jobs)}"
    )

    return jobs


# ============================================================
# SEEN JOBS
# ============================================================

def load_seen():
    """
    Load permanent job history.

    There is intentionally NO expiration.
    """

    if not os.path.exists(
        SEEN_FILE
    ):
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

        # Support an older format:
        # {"urls": ["url1", "url2"]}
        if isinstance(data, list):
            return {
                str(url): {}
                for url in data
            }

        print(
            "Invalid seen.json format. "
            "Starting with empty history."
        )

        return {}

    except Exception as error:

        print(
            f"Could not load {SEEN_FILE}: "
            f"{error}"
        )

        return {}


def save_seen(data):
    """
    Save seen.json atomically.
    """

    temporary_file = (
        f"{SEEN_FILE}.tmp"
    )

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
            sort_keys=True,
        )

    os.replace(
        temporary_file,
        SEEN_FILE,
    )


# ============================================================
# EMAIL
# ============================================================

def build_email(jobs):
    rows = []

    for job in jobs:

        safe_title = html.escape(
            job["title"]
        )

        safe_company = html.escape(
            job["company"]
        )

        safe_location = html.escape(
            job["location"]
        )

        safe_source = html.escape(
            job["source"]
        )

        safe_link = html.escape(
            job["link"],
            quote=True,
        )

        rows.append(
            f"""
            <tr>
                <td>{safe_title}</td>
                <td>{safe_company}</td>
                <td>{safe_location}</td>
                <td>{safe_source}</td>
                <td>
                    <a href="{safe_link}">
                        View Job
                    </a>
                </td>
            </tr>
            """
        )

    return f"""
    <html>
    <body>

        <h3>Job Alerts</h3>

        <p>
            Found {len(jobs)}
            new job(s).
        </p>

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

            {''.join(rows)}

        </table>

    </body>
    </html>
    """


def send_email(jobs):
    """
    Gmail is OPTIONAL.

    Email failure does NOT determine whether a job
    is marked as seen.

    Telegram is the primary notification channel.
    """

    if not jobs:
        return True

    email_user = os.environ.get(
        "EMAIL_USER"
    )

    email_pass = os.environ.get(
        "EMAIL_PASS"
    )

    email_to = os.environ.get(
        "EMAIL_TO"
    )

    if (
        not email_user
        or not email_pass
        or not email_to
    ):

        print(
            "Gmail is not configured. "
            "Skipping Gmail notification."
        )

        return True

    try:

        message = MIMEText(
            build_email(jobs),
            "html",
        )

        message["Subject"] = (
            "Job Alerts"
        )

        message["From"] = email_user
        message["To"] = email_to

        with smtplib.SMTP_SSL(
            "smtp.gmail.com",
            465,
            timeout=REQUEST_TIMEOUT,
        ) as server:

            server.login(
                email_user,
                email_pass,
            )

            server.send_message(
                message
            )

        print(
            "Gmail notification succeeded."
        )

        return True

    except Exception as error:

        print(
            f"WARNING: Gmail notification "
            f"failed: {error}"
        )

        return False


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_MAX_MESSAGE_LENGTH = 4000


def build_telegram_job_text(job):
    """
    Build one Telegram job entry.
    """

    safe_title = html.escape(
        job["title"]
    )

    safe_company = html.escape(
        job["company"]
    )

    safe_location = html.escape(
        job["location"]
    )

    safe_source = html.escape(
        job["source"]
    )

    safe_link = html.escape(
        job["link"],
        quote=True,
    )

    return (
        f"• <b>{safe_title}</b>\n"
        f"{safe_company} | "
        f"{safe_location}\n"
        f"[{safe_source}] "
        f"<a href=\"{safe_link}\">"
        f"View Job"
        f"</a>\n\n"
    )


def split_telegram_messages(jobs):
    """
    Split jobs into multiple Telegram messages.

    Telegram has a message-length limit, so a large batch
    must not be sent as one request.
    """

    header = (
        "<b>New Job Alerts</b>\n\n"
    )

    messages = []
    current = header

    for job in jobs:

        job_text = (
            build_telegram_job_text(
                job
            )
        )

        # If adding this job would exceed the limit,
        # start a new message.
        if (
            len(current)
            + len(job_text)
            > TELEGRAM_MAX_MESSAGE_LENGTH
        ):

            if current != header:
                messages.append(
                    current
                )

            current = (
                header
                + job_text
            )

        else:

            current += job_text

    if current != header:
        messages.append(
            current
        )

    return messages


def send_telegram(jobs):
    """
    Telegram is the PRIMARY notification channel.

    ALL Telegram message chunks must succeed.

    Returns:
        True  = all Telegram messages succeeded
        False = at least one Telegram message failed
    """

    if not jobs:
        return True

    token = os.environ.get(
        "TG_BOT_TOKEN"
    )

    chat_id = os.environ.get(
        "TG_CHAT_ID"
    )

    if not token or not chat_id:

        print(
            "ERROR: Telegram credentials "
            "are not configured."
        )

        return False

    messages = split_telegram_messages(
        jobs
    )

    print(
        f"Telegram: sending "
        f"{len(messages)} message(s)"
    )

    url = (
        "https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    for index, message in enumerate(
        messages,
        start=1,
    ):

        try:

            response = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            result = response.json()

            if not result.get("ok"):

                raise RuntimeError(
                    f"Telegram API error: "
                    f"{result}"
                )

            print(
                f"Telegram message "
                f"{index}/{len(messages)} "
                f"succeeded."
            )

        except Exception as error:

            print(
                f"ERROR: Telegram message "
                f"{index}/{len(messages)} "
                f"failed: {error}"
            )

            return False

    print(
        "Telegram notification "
        "succeeded for all messages."
    )

    return True


# ============================================================
# NOTIFICATION
# ============================================================

def notify(jobs):
    """
    Notification rules:

      Telegram succeeds + Gmail succeeds
          -> SUCCESS / mark seen

      Telegram succeeds + Gmail fails
          -> SUCCESS / mark seen

      Telegram succeeds + Gmail disabled
          -> SUCCESS / mark seen

      Telegram fails + Gmail succeeds
          -> FAILURE / do NOT mark seen

      Telegram fails + Gmail fails
          -> FAILURE / do NOT mark seen

    Telegram is therefore the primary channel.
    """

    if not jobs:
        return True

    telegram_configured = bool(
        os.environ.get(
            "TG_BOT_TOKEN"
        )
        and os.environ.get(
            "TG_CHAT_ID"
        )
    )

    email_configured = bool(
        os.environ.get(
            "EMAIL_USER"
        )
        and os.environ.get(
            "EMAIL_PASS"
        )
        and os.environ.get(
            "EMAIL_TO"
        )
    )

    # Telegram is mandatory.
    if not telegram_configured:

        print(
            "ERROR: Telegram is not configured."
        )

        print(
            "Jobs will NOT be marked as seen."
        )

        return False

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    telegram_ok = send_telegram(
        jobs
    )

    # --------------------------------------------------------
    # Gmail
    # --------------------------------------------------------

    email_ok = True

    if email_configured:

        email_ok = send_email(
            jobs
        )

        if not email_ok:

            print(
                "WARNING: Gmail notification "
                "failed, but this does NOT "
                "affect seen status because "
                "Telegram is the primary channel."
            )

    else:

        print(
            "Gmail is not configured. "
            "Telegram is sufficient."
        )

    # --------------------------------------------------------
    # Final notification decision
    # --------------------------------------------------------

    if telegram_ok:

        print(
            "Telegram succeeded. "
            "Jobs WILL be marked as seen."
        )

        if email_configured and not email_ok:

            print(
                "Gmail failed, but jobs "
                "will still be marked as seen."
            )

        return True

    print(
        "Telegram failed. "
        "Jobs will NOT be marked as seen."
    )

    return False


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("Scrum Master Job Alerts")
    print("=" * 60)

    # --------------------------------------------------------
    # Fetch jobs
    # --------------------------------------------------------

    jobs = fetch_all()

    # --------------------------------------------------------
    # Load permanent history
    # --------------------------------------------------------

    seen = load_seen()

    print(
        f"Previously seen jobs: "
        f"{len(seen)}"
    )

    # --------------------------------------------------------
    # Find unseen jobs
    # --------------------------------------------------------

    new_jobs = []

    for job in jobs:

        key = job_id(
            job
        )

        if key not in seen:
            new_jobs.append(
                job
            )

    print(
        f"New jobs: "
        f"{len(new_jobs)}"
    )

    # --------------------------------------------------------
    # Nothing new
    # --------------------------------------------------------

    if not new_jobs:

        print(
            "No new jobs."
        )

        return

    # --------------------------------------------------------
    # Display new jobs
    # --------------------------------------------------------

    print(
        "New jobs:"
    )

    for job in new_jobs:

        print(
            f"  - "
            f"{job['title']} | "
            f"{job['company']} | "
            f"{job['location']} | "
            f"{job['source']}"
        )

    # --------------------------------------------------------
    # Notify
    # --------------------------------------------------------

    notification_success = notify(
        new_jobs
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Only Telegram determines whether the job
    # is successfully alerted.
    #
    # Gmail failure does NOT matter here.
    # --------------------------------------------------------

    if not notification_success:

        print(
            "Notification was not successfully "
            "delivered through Telegram."
        )

        print(
            "Jobs will remain unseen and "
            "will be retried next run."
        )

        return

    # --------------------------------------------------------
    # Telegram succeeded.
    #
    # Permanently save jobs as seen.
    # --------------------------------------------------------

    updated_seen = seen.copy()

    for job in new_jobs:

        key = job_id(
            job
        )

        updated_seen[key] = {
            "title": job["title"],
            "company": job["company"],
            "location": job["location"],
            "link": job["link"],
            "source": job["source"],
        }

    save_seen(
        updated_seen
    )

    print(
        f"Successfully saved "
        f"{len(new_jobs)} job(s) "
        f"to seen.json."
    )

    print(
        f"Seen history now contains "
        f"{len(updated_seen)} job(s)."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
