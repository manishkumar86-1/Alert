import html
import json
import os
import re
import smtplib
from email.mime.text import MIMEText
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

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
# INDEED VIA GOOGLE SEARCH
# ============================================================

GOOGLE_SEARCH_URL = "https://www.google.com/search"
GOOGLE_SEARCH_RESULTS = 20


def extract_google_result_url(raw_href):
    """
    Convert a Google result link into the underlying Indeed URL.

    Google can return either:
        https://ca.indeed.com/viewjob?jk=...
    or a Google redirect such as:
        /url?q=https://ca.indeed.com/viewjob?jk=...
    """

    if not raw_href:
        return ""

    href = html.unescape(raw_href.strip())

    if href.startswith("//"):
        href = "https:" + href

    parsed = urlparse(href)

    # Google redirect URL.
    if parsed.netloc.endswith("google.com") or parsed.netloc.endswith(
        "google.ca"
    ):
        query = parse_qs(parsed.query)

        for key in ("q", "url"):
            values = query.get(key)
            if values:
                href = unquote(values[0])
                break

    # Relative Google redirect URL.
    elif href.startswith("/url?"):
        query = parse_qs(parsed.query)

        for key in ("q", "url"):
            values = query.get(key)
            if values:
                href = unquote(values[0])
                break

    return href


def is_indeed_job_url(url):
    """
    Accept actual Indeed job pages, not Indeed search/category pages.
    """

    if not url:
        return False

    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if not host.endswith("indeed.com"):
        return False

    path = parsed.path.lower()

    return (
        "/viewjob" in path
        or "/pagead/" in path
    )



def clean_indeed_title(title):
    """
    Remove Google's Indeed page-title suffix so the alert shows
    the actual job title instead of the search-engine page title.
    """

    title = " ".join(
        title.split()
    ).strip()

    # Remove the standard Indeed suffix.
    title = re.sub(
        r"\s+-\s+Indeed(?:\.com)?$",
        "",
        title,
        flags=re.IGNORECASE,
    )

    # Remove common location suffixes that Google includes in
    # the page title. Keep the actual role title intact.
    location_suffix = (
        r"(?:Toronto|North York|Scarborough|Etobicoke|Markham|"
        r"Richmond Hill|Vaughan|Thornhill|Mississauga|Brampton|"
        r"Oakville|Ajax|Pickering|Whitby|Oshawa|Greater Toronto Area)"
        r"(?:,\s*ON)?"
    )

    title = re.sub(
        rf"\s+-\s+{location_suffix}\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    )

    title = re.sub(
        r"\s+-\s+Canada\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    )

    return title.strip(" -")


def extract_indeed_location(text):
    """
    Infer a location from the Google result title/snippet.

    We deliberately require the location to appear in the actual
    Google result content; the search query itself is not counted.
    """

    normalized = " ".join(
        text.lower().split()
    )

    # More specific GTA phrases first.
    location_patterns = [
        ("greater toronto area", "Greater Toronto Area, ON"),
        ("north york", "North York, ON"),
        ("scarborough", "Scarborough, ON"),
        ("etobicoke", "Etobicoke, ON"),
        ("markham", "Markham, ON"),
        ("richmond hill", "Richmond Hill, ON"),
        ("vaughan", "Vaughan, ON"),
        ("thornhill", "Thornhill, ON"),
        ("mississauga", "Mississauga, ON"),
        ("brampton", "Brampton, ON"),
        ("oakville", "Oakville, ON"),
        ("ajax", "Ajax, ON"),
        ("pickering", "Pickering, ON"),
        ("whitby", "Whitby, ON"),
        ("oshawa", "Oshawa, ON"),
        ("toronto", "Toronto, ON"),
        ("gta", "GTA"),
    ]

    for needle, location in location_patterns:
        if needle in normalized:
            return location

    # Remote Canada variations.
    remote_canada_patterns = [
        "remote canada",
        "canada remote",
        "remote - canada",
        "remote, canada",
        "canada (remote)",
        "canada (remote work)",
        "anywhere in canada",
        "nationwide canada",
    ]

    if any(
        pattern in normalized
        for pattern in remote_canada_patterns
    ):
        return "Remote, Canada"

    # A result can show "Remote" and "Canada" separately.
    if "remote" in normalized and "canada" in normalized:
        return "Remote, Canada"

    return ""


def extract_indeed_company(snippet):
    """
    Google snippets for Indeed often start with the employer name,
    followed by a rating and/or location. Extract that when possible.

    Company extraction is best-effort because we are intentionally
    not opening Indeed pages after discovery.
    """

    if not snippet:
        return "Unknown"

    text = " ".join(
        snippet.split()
    ).strip()

    # Common Indeed pattern:
    # Company Name 4.2 4.2 out of 5 stars ...
    match = re.match(
        r"^(.{2,100}?)\s+"
        r"\d(?:\.\d)?(?:\s+\d(?:\.\d)?)?"
        r"(?:\s+out of 5 stars?)?",
        text,
        flags=re.IGNORECASE,
    )

    if match:
        company = match.group(1).strip(" -|·")
        if company:
            return company

    # Another common pattern:
    # Company Name · 4.2 · Toronto, ON
    parts = [
        part.strip(" -|")
        for part in re.split(
            r"\s+[·•]\s+",
            text,
        )
        if part.strip()
    ]

    if parts:
        first = parts[0]

        if (
            first.lower() not in {"indeed", "indeed.com"}
            and len(first) <= 100
            and not re.search(r"\bremote\b|\bcanada\b", first, re.I)
        ):
            return first

    return "Unknown"


def fetch_google_indeed(search_name, query):
    """
    Search Google for Indeed job pages.

    Google is used only as a discovery mechanism. We do not call
    an API and we do not request the Indeed search page, which was
    returning HTTP 403 from GitHub Actions.

    The qdr:d parameter asks Google for results from the past day.
    We still apply our own title/location filters afterward.
    """

    print(
        f"Searching Indeed through Google: {search_name}"
    )

    params = {
        "q": query,
        "hl": "en",
        "gl": "ca",
        "num": GOOGLE_SEARCH_RESULTS,
        "filter": "0",
        "tbs": "qdr:d",
    }

    try:
        response = requests.get(
            GOOGLE_SEARCH_URL,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # Google result blocks normally use MjjYud. The fallback
        # scans anchors containing actual Indeed job URLs.
        result_blocks = soup.select(
            "div.MjjYud"
        )

        results = []

        if result_blocks:
            for block in result_blocks:

                link_el = block.select_one(
                    "a[href]"
                )

                title_el = block.select_one(
                    "h3"
                )

                snippet_el = block.select_one(
                    ".VwiC3b, .yXK7lf, div[data-sncf]"
                )

                if not link_el:
                    continue

                raw_href = link_el.get("href", "")
                link = extract_google_result_url(
                    raw_href
                )

                if not is_indeed_job_url(link):
                    continue

                title = (
                    title_el.get_text(
                        " ",
                        strip=True,
                    )
                    if title_el
                    else ""
                )

                snippet = (
                    snippet_el.get_text(
                        " ",
                        strip=True,
                    )
                    if snippet_el
                    else block.get_text(
                        " ",
                        strip=True,
                    )
                )

                results.append(
                    {
                        "title": title,
                        "snippet": snippet,
                        "link": link,
                    }
                )

        # Fallback for Google markup changes.
        if not results:
            for link_el in soup.select(
                "a[href]"
            ):

                raw_href = link_el.get(
                    "href",
                    "",
                )

                link = extract_google_result_url(
                    raw_href
                )

                if not is_indeed_job_url(link):
                    continue

                title_el = link_el.select_one(
                    "h3"
                )

                title = (
                    title_el.get_text(
                        " ",
                        strip=True,
                    )
                    if title_el
                    else link_el.get_text(
                        " ",
                        strip=True,
                    )
                )

                parent = link_el.parent
                snippet = (
                    parent.get_text(
                        " ",
                        strip=True,
                    )
                    if parent
                    else ""
                )

                results.append(
                    {
                        "title": title,
                        "snippet": snippet,
                        "link": link,
                    }
                )

        # Deduplicate Google results before filtering.
        unique_results = {}

        for result in results:
            unique_results[result["link"]] = result

        results = list(
            unique_results.values()
        )

        print(
            f"Google {search_name} "
            f"Indeed job links found: "
            f"{len(results)}"
        )

        jobs = []

        for result in results:

            title = clean_indeed_title(
                result["title"]
            )
            snippet = result["snippet"].strip()

            combined_text = (
                f"{title} {snippet}"
            )

            location = extract_indeed_location(
                combined_text
            )

            company = extract_indeed_company(
                snippet
            )

            job = {
                "title": title,
                "company": company,
                "location": location,
                "link": normalize_url(
                    result["link"],
                    "https://ca.indeed.com",
                ),
                "source": "Indeed",
            }

            if is_relevant(job):
                jobs.append(job)

        print(
            f"Google {search_name} "
            f"relevant Indeed jobs: "
            f"{len(jobs)}"
        )

        return jobs

    except requests.RequestException as error:
        print(
            f"Google {search_name} "
            f"request error: {error}"
        )
        return []

    except Exception as error:
        print(
            f"Google {search_name} "
            f"error: {error}"
        )
        return []


def fetch_indeed():
    """
    Discover Indeed jobs through Google.

    Two searches are performed:

    1. Scrum Master + Toronto/GTA
       Google past-day filter

    2. Scrum Master + Remote Canada
       Google past-day filter

    The returned jobs are still passed through the same strict
    Scrum Master/location filter used by the rest of the bot.
    """

    toronto_locations_query = (
        '("Toronto" OR "Greater Toronto Area" OR '
        '"North York" OR "Scarborough" OR "Etobicoke" OR '
        '"Markham" OR "Richmond Hill" OR "Vaughan" OR '
        '"Thornhill" OR "Mississauga" OR "Brampton" OR '
        '"Oakville" OR "Ajax" OR "Pickering" OR "Whitby" OR '
        '"Oshawa" OR "GTA")'
    )

    searches = [
        (
            "Toronto",
            (
                'site:indeed.com/viewjob '
                'intitle:"Scrum Master" '
                f"{toronto_locations_query}"
            ),
        ),
        (
            "Remote Canada",
            (
                'site:indeed.com/viewjob '
                'intitle:"Scrum Master" '
                '("remote" OR "work from home") '
                'Canada'
            ),
        ),
    ]

    jobs = []

    for search_name, query in searches:
        jobs.extend(
            fetch_google_indeed(
                search_name,
                query,
            )
        )

    # Deduplicate jobs discovered by both searches.
    unique_jobs = {}

    for job in jobs:
        unique_jobs[job_id(job)] = job

    jobs = list(
        unique_jobs.values()
    )

    print(
        f"Indeed Scrum Master jobs "
        f"(via Google): {len(jobs)}"
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
