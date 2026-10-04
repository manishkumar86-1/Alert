import html
import json
import os
import smtplib
from email.mime.text import MIMEText
from urllib.parse import urljoin, urlparse, quote_plus

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
    "gta",
]


# ============================================================
# JOB FILTER
# ============================================================

def is_relevant(job):
    """
    Temporary LinkedIn test filter.

    Accept jobs based ONLY on:
    1. Toronto / GTA location
    2. Remote Canada location

    No job-title filtering is performed here.
    """

    location = job.get(
        "location",
        ""
    ).strip().lower()

    if not location:
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
    Convert a relative URL into an absolute URL
    and remove URL fragments.
    """

    if not url:
        return ""

    absolute_url = urljoin(
        base_url,
        url.strip(),
    )

    parsed = urlparse(absolute_url)

    return parsed._replace(
        fragment=""
    ).geturl()


# ============================================================
# JOB ID
# ============================================================

def job_id(job):
    """
    Use the actual posting URL as the primary identifier.

    If a source does not provide a URL, fall back to:
    title + company + location.
    """

    link = job.get(
        "link",
        "",
    ).strip()

    if link:
        return link

    title = job.get(
        "title",
        "",
    ).strip().lower()

    company = job.get(
        "company",
        "",
    ).strip().lower()

    location = job.get(
        "location",
        "",
    ).strip().lower()

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
    Existing Indeed implementation.

    Search:
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

        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )

            cards = soup.select(
                "a.tapItem"
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

                    if (
                        "scrum master"
                        in title.lower()
                    ):
                        if is_relevant(job):
                            jobs.append(job)

                except Exception:
                    continue

        except requests.RequestException:
            continue

        except Exception:
            continue

    return jobs


# ============================================================
# LINKEDIN
# ============================================================

def fetch_linkedin():
    """
    Temporary LinkedIn test.

    Search 1:
        Toronto
        Last 1 hour

    Search 2:
        Remote Canada
        Last 1 hour

    IMPORTANT:
    There is intentionally NO keywords parameter.

    The current test is designed to determine whether
    LinkedIn returns location/time-filtered jobs correctly.
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

        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )

            cards = soup.select(
                "li"
            )

            for card in cards:

                try:
                    title_el = card.select_one(
                        "h3"
                    )

                    company_el = card.select_one(
                        "h4"
                    )

                    location_el = card.select_one(
                        ".job-search-card__location"
                    )

                    link_el = card.select_one(
                        "a.base-card__full-link"
                    )

                    if not link_el:
                        link_el = card.select_one(
                            "a"
                        )

                    if not title_el or not link_el:
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

                    if is_relevant(job):
                        jobs.append(job)

                except Exception:
                    continue

        except requests.RequestException as error:
            print(
                f"LinkedIn {search_name} request failed: "
                f"{error}"
            )

        except Exception as error:
            print(
                f"LinkedIn {search_name} failed: "
                f"{error}"
            )

    return jobs


# ============================================================
# FETCH ALL SOURCES
# ============================================================

def fetch_all():
    """
    Fetch Indeed and LinkedIn jobs
    and remove duplicates.
    """

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

    return list(
        unique_jobs.values()
    )


# ============================================================
# SEEN JOBS
# ============================================================

def load_seen():
    """
    Load permanently seen jobs.

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

        if isinstance(
            data,
            dict,
        ):
            return data

        return {}

    except Exception:
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
    Send email if email credentials are configured.

    Email is optional.
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
        return True

    try:

        message = MIMEText(
            build_email(jobs),
            "html",
        )

        message["Subject"] = (
            "Scrum Master Job Alerts"
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

        return True

    except Exception as error:

        print(
            f"Email error: {error}"
        )

        return False


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(jobs):
    """
    Send new jobs to Telegram.
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
            "Telegram credentials "
            "are not configured."
        )

        return False

    try:

        message = (
            "<b>New Job Alerts</b>\n\n"
        )

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

            message += (
                f"• <b>{safe_title}</b>\n"
                f"{safe_company} | "
                f"{safe_location}\n"
                f"[{safe_source}] "
                f"<a href=\"{safe_link}\">"
                f"View Job"
                f"</a>\n\n"
            )

        url = (
            "https://api.telegram.org/"
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
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        result = response.json()

        if not result.get(
            "ok"
        ):
            raise RuntimeError(
                f"Telegram API error: "
                f"{result}"
            )

        return True

    except Exception as error:

        print(
            f"Telegram error: {error}"
        )

        return False


# ============================================================
# MAIN
# ============================================================

def main():

    jobs = fetch_all()

    seen = load_seen()

    new_jobs = []

    for job in jobs:

        key = job_id(job)

        if key not in seen:
            new_jobs.append(job)

    print(
        f"Jobs found: {len(jobs)}"
    )

    print(
        f"New jobs: {len(new_jobs)}"
    )

    if not new_jobs:
        print(
            "No new jobs."
        )
        return

    print(
        "New job alerts:"
    )

    for job in new_jobs:

        print(
            f"  - {job['title']} | "
            f"{job['company']} | "
            f"{job['location']} | "
            f"{job['source']}"
        )

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

    if (
        not telegram_configured
        and not email_configured
    ):
        raise RuntimeError(
            "No notification channel is configured."
        )

    telegram_ok = True
    email_ok = True

    if telegram_configured:
        telegram_ok = send_telegram(
            new_jobs
        )

    if email_configured:
        email_ok = send_email(
            new_jobs
        )

    if not telegram_ok:
        raise RuntimeError(
            "Telegram notification failed. "
            "Jobs will not be marked as seen."
        )

    if not email_ok:
        raise RuntimeError(
            "Email notification failed. "
            "Jobs will not be marked as seen."
        )

    # Only mark jobs as seen after all
    # configured notifications succeed.

    for job in new_jobs:

        key = job_id(job)

        seen[key] = {
            "title": job["title"],
            "company": job["company"],
            "location": job["location"],
            "source": job["source"],
            "link": job["link"],
        }

    save_seen(seen)

    print(
        f"Saved {len(new_jobs)} new job(s) "
        "to seen.json."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
