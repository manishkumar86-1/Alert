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
    "gta",
]


# ============================================================
# JOB FILTER
# ============================================================

def is_relevant(job):
    """
    Return True only for Scrum Master jobs in:

    1. Toronto / GTA
    2. Remote Canada

    Other roles such as QA, SDET, Product Owner,
    Project Manager, etc. are rejected.
    """

    title = job.get("title", "").strip().lower()
    location = job.get("location", "").strip().lower()

    if not title:
        return False

    # --------------------------------------------------------
    # ONLY Scrum Master roles
    # --------------------------------------------------------

    if "scrum master" not in title:
        return False

    # --------------------------------------------------------
    # Reject obvious training/certification results
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Toronto / GTA
    # --------------------------------------------------------

    toronto_match = any(
        location_name in location
        for location_name in TORONTO_LOCATIONS
    )

    # --------------------------------------------------------
    # Remote Canada
    #
    # Different job sites can display remote locations as:
    #
    # Remote, Canada
    # Remote - Canada
    # Canada - Remote
    # Anywhere in Canada
    # Canada (Remote)
    # Nationwide
    # --------------------------------------------------------

    remote_canada_match = (
        "remote" in location
        and (
            "canada" in location
            or "nationwide" in location
            or "anywhere in canada" in location
        )
    )

    if not toronto_match and not remote_canada_match:
        return False

    return True


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

    This allows the bot to permanently remember jobs it
    has already shown.

    If a source does not provide a URL, fall back to:
        title + company + location
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
    Search Indeed for:

    1. Scrum Master + Toronto
       Last 1 day

    2. Scrum Master + Remote Canada
       Last 1 day

    Uses Indeed's current search-result URL format rather than
    relying exclusively on the /jobs endpoint.
    """

    search_urls = [
        (
            "Toronto",
            (
                "https://ca.indeed.com/"
                "q-scrum-master-l-toronto%2C-on-jobs.html"
                "?fromage=1"
            ),
        ),
        (
            "Remote Canada",
            (
                "https://ca.indeed.com/"
                "q-remote-scrum-master-l-canada-jobs.html"
                "?fromage=1"
            ),
        ),
    ]

    jobs = []

    for search_name, url in search_urls:

        print(
            f"Searching Indeed: {search_name}"
        )
        print(
            f"Indeed URL: {url}"
        )

        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            print(
                f"Indeed {search_name} HTTP status: "
                f"{response.status_code}"
            )

            print(
                f"Indeed {search_name} final URL: "
                f"{response.url}"
            )

            if response.status_code == 403:
                print(
                    f"Indeed {search_name} returned HTTP 403. "
                    "Indeed is blocking this GitHub Actions request."
                )
                continue

            response.raise_for_status()

            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )

            cards = soup.select(
                "a.tapItem, "
                "div.job_seen_beacon, "
                "div.cardOutline"
            )

            print(
                f"Indeed {search_name} "
                f"candidate cards found: {len(cards)}"
            )

            parsed_links = set()

            for card in cards:

                try:
                    title_el = (
                        card.select_one("h2 span")
                        or card.select_one("h2.jobTitle span")
                        or card.select_one("h2.jobTitle")
                    )

                    company_el = (
                        card.select_one(".companyName")
                        or card.select_one(
                            "[data-testid='company-name']"
                        )
                    )

                    location_el = (
                        card.select_one(".companyLocation")
                        or card.select_one(
                            "[data-testid='text-location']"
                        )
                    )

                    link_el = (
                        card
                        if card.name == "a"
                        and card.get("href")
                        else card.select_one(
                            "a[href*='/viewjob']"
                        )
                    )

                    if not link_el:
                        link_el = card.select_one(
                            "a[href*='/rc/clk']"
                        )

                    if not title_el or not link_el:
                        continue

                    raw_link = link_el.get("href")

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

                    if link in parsed_links:
                        continue

                    parsed_links.add(link)

                    job = {
                        "title": title,
                        "company": company,
                        "location": location,
                        "link": link,
                        "source": "Indeed",
                    }

                    if is_relevant(job):
                        print(
                            f"Indeed relevant job: "
                            f"{title} | {company} | {location}"
                        )
                        jobs.append(job)

                except Exception as error:
                    print(
                        f"Indeed {search_name} card error: "
                        f"{error}"
                    )
                    continue

            if not cards:
                print(
                    f"Indeed {search_name}: "
                    "No recognized cards. Trying direct job links."
                )

                job_links = soup.select(
                    "a[href*='/viewjob'], "
                    "a[href*='/rc/clk']"
                )

                print(
                    f"Indeed {search_name} direct job links: "
                    f"{len(job_links)}"
                )

                for link_el in job_links:

                    try:
                        raw_link = link_el.get("href")

                        if not raw_link:
                            continue

                        link = normalize_url(
                            raw_link,
                            "https://ca.indeed.com",
                        )

                        if link in parsed_links:
                            continue

                        title = link_el.get_text(
                            " ",
                            strip=True,
                        )

                        if not title:
                            continue

                        parsed_links.add(link)

                        job = {
                            "title": title,
                            "company": "Unknown",
                            "location": "Unknown",
                            "link": link,
                            "source": "Indeed",
                        }

                        if is_relevant(job):
                            print(
                                f"Indeed relevant job: {title}"
                            )
                            jobs.append(job)

                    except Exception as error:
                        print(
                            f"Indeed {search_name} link error: "
                            f"{error}"
                        )
                        continue

            print(
                f"Indeed {search_name} relevant jobs found: "
                f"{sum(1 for job in jobs if job['source'] == 'Indeed')}"
            )

        except requests.RequestException as error:
            print(
                f"Indeed {search_name} request error: "
                f"{error}"
            )

        except Exception as error:
            print(
                f"Indeed {search_name} error: "
                f"{error}"
            )

    return jobs


# ============================================================
# LINKEDIN
# ============================================================

def fetch_linkedin():
    """
    Search LinkedIn for:

    1. Scrum Master + Toronto
       Approximately last 1 hour

    2. Scrum Master + Remote Canada
       Approximately last 1 hour
    """

    search_urls = [
        (
            "Toronto",
            (
                "https://www.linkedin.com/jobs/search/"
                "?keywords=Scrum%20Master"
                "&location=Toronto"
                "&f_TPR=r3600"
            ),
        ),
        (
            "Remote Canada",
            (
                "https://www.linkedin.com/jobs/search/"
                "?keywords=Scrum%20Master"
                "&location=Canada"
                "&f_WT=2"
                "&f_TPR=r3600"
            ),
        ),
    ]

    jobs = []

    for search_name, url in search_urls:

        print(
            f"Searching LinkedIn: {search_name}"
        )

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

            cards = soup.select("li")

            print(
                f"LinkedIn {search_name} "
                f"cards found: {len(cards)}"
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

                    # Fallback if LinkedIn changes
                    # the link class.
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

                except Exception as error:
                    print(
                        f"LinkedIn card error: {error}"
                    )
                    continue

        except requests.RequestException as error:
            print(
                f"LinkedIn {search_name} "
                f"request error: {error}"
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
    """
    Fetch from Indeed and LinkedIn and remove
    duplicates found during this run.
    """

    indeed_jobs = fetch_indeed()

    linkedin_jobs = fetch_linkedin()

    jobs = indeed_jobs + linkedin_jobs

    unique_jobs = {}

    for job in jobs:
        unique_jobs[job_id(job)] = job

    jobs = list(
        unique_jobs.values()
    )

    print(
        f"Indeed Scrum Master jobs: "
        f"{len(indeed_jobs)}"
    )

    print(
        f"LinkedIn Scrum Master jobs: "
        f"{len(linkedin_jobs)}"
    )

    print(
        f"Unique relevant jobs: "
        f"{len(jobs)}"
    )

    return jobs


# ============================================================
# SEEN JOBS
# ============================================================

def load_seen():
    """
    Load permanently seen jobs.

    There is intentionally NO expiration.

    This prevents the same posting from being shown
    again weeks or months later if the same URL is reused.
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

        print(
            "seen.json does not contain "
            "a JSON object."
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

        <h3>New Scrum Master Job Alerts</h3>

        <p>
            Found {len(jobs)}
            new Scrum Master job(s).
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

    # Email is optional.
    if not email_user or not email_pass or not email_to:

        print(
            "Email is not configured. "
            "Skipping email notification."
        )

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

        print(
            "Email sent successfully."
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

    Telegram is the primary notification channel.
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
            "<b>New Scrum Master Job Alerts</b>\n\n"
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

        if not result.get("ok"):

            raise RuntimeError(
                f"Telegram API error: "
                f"{result}"
            )

        print(
            "Telegram message sent successfully."
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

    print("=" * 60)
    print("Scrum Master Job Alerts")
    print("=" * 60)

    # --------------------------------------------------------
    # Search
    # --------------------------------------------------------

    jobs = fetch_all()

    # --------------------------------------------------------
    # Load permanent history
    # --------------------------------------------------------

    seen = load_seen()

    # --------------------------------------------------------
    # Find jobs we have never notified about
    # --------------------------------------------------------

    new_jobs = []

    for job in jobs:

        key = job_id(job)

        if key not in seen:
            new_jobs.append(job)

    print(
        f"New Scrum Master jobs: "
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
    # Display jobs in GitHub Actions log
    # --------------------------------------------------------

    print("New jobs:")

    for job in new_jobs:

        print(
            f"  - {job['title']} | "
            f"{job['company']} | "
            f"{job['location']} | "
            f"{job['source']}"
        )

    # --------------------------------------------------------
    # Notification configuration
    # --------------------------------------------------------

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
            "No notification channel "
            "is configured. "
            "Configure Telegram or Email."
        )

    notification_results = []

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    if telegram_configured:

        telegram_success = (
            send_telegram(
                new_jobs
            )
        )

        notification_results.append(
            telegram_success
        )

    # --------------------------------------------------------
    # Email
    # --------------------------------------------------------

    if email_configured:

        email_success = (
            send_email(
                new_jobs
            )
        )

        notification_results.append(
            email_success
        )

    # --------------------------------------------------------
    # IMPORTANT
    #
    # Do NOT update seen.json if any configured
    # notification channel failed.
    #
    # This means the job will be retried on the
    # next workflow execution.
    # --------------------------------------------------------

    if not all(
        notification_results
    ):

        raise RuntimeError(
            "One or more notifications failed. "
            "Jobs will NOT be marked as seen."
        )

    # --------------------------------------------------------
    # All notifications succeeded.
    #
    # Permanently save the jobs.
    # --------------------------------------------------------

    updated_seen = seen.copy()

    for job in new_jobs:

        key = job_id(job)

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
        f"{len(new_jobs)} new jobs "
        f"to seen.json."
    )


if __name__ == "__main__":
    main()
