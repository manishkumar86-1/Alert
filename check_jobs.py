import html
import json
import os
import re
import smtplib
from email.mime.text import MIMEText
from urllib.parse import (
    parse_qs,
    unquote,
    urljoin,
    urlparse,
)

import requests
from bs4 import BeautifulSoup


SEEN_FILE = "seen.json"
REQUEST_TIMEOUT = 20

GOOGLE_SEARCH_URL = "https://www.google.com/search"
GOOGLE_SEARCH_RESULTS = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,image/webp,"
        "*/*;q=0.8"
    ),
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

    # ONLY Scrum Master roles.
    if "scrum master" not in title:
        return False

    # Reject obvious training/certification results.
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

    # Toronto / GTA.
    toronto_match = any(
        location_name in location
        for location_name in TORONTO_LOCATIONS
    )

    # Remote Canada.
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

    seen.json has no expiration.

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
# GOOGLE / INDEED URL HELPERS
# ============================================================

def extract_google_result_url(raw_href):
    """
    Convert a Google result link into the underlying URL.

    Google can return:

        https://ca.indeed.com/viewjob?jk=...

    or:

        /url?q=https://ca.indeed.com/viewjob?jk=...

    or:

        https://www.google.com/url?url=...
    """

    if not raw_href:
        return ""

    href = html.unescape(
        raw_href.strip()
    )

    if not href:
        return ""

    if href.startswith("//"):
        href = "https:" + href

    # Relative Google redirect.
    if href.startswith("/url?"):
        parsed = urlparse(href)
        query = parse_qs(parsed.query)

        for key in ("q", "url"):
            values = query.get(key)

            if values:
                return unquote(values[0])

        return href

    parsed = urlparse(href)

    # Absolute Google redirect.
    if (
        parsed.netloc.lower().endswith("google.com")
        or parsed.netloc.lower().endswith("google.ca")
    ):
        query = parse_qs(parsed.query)

        for key in ("q", "url"):
            values = query.get(key)

            if values:
                return unquote(values[0])

    return href


def is_indeed_domain_url(url):
    """
    Return True for any Indeed URL.

    This is intentionally broader than is_indeed_job_url()
    because it is used for diagnostics.
    """

    if not url:
        return False

    parsed = urlparse(url)

    host = parsed.netloc.lower().split(":", 1)[0]

    return host.endswith("indeed.com")


def is_indeed_job_url(url):
    """
    Accept common Indeed job URL formats.

    Examples:

        /viewjob?jk=...
        /rc/clk?jk=...
        /pagead/clk?jk=...
    """

    if not url:
        return False

    parsed = urlparse(url)

    host = parsed.netloc.lower().split(":", 1)[0]

    if not host.endswith("indeed.com"):
        return False

    path = parsed.path.lower()
    query = parsed.query.lower()

    if "/viewjob" in path:
        return True

    if "/rc/clk" in path and "jk=" in query:
        return True

    if "/pagead/" in path and "jk=" in query:
        return True

    return False


def normalize_indeed_job_url(url):
    """
    Normalize an Indeed URL.

    For standard viewjob URLs, keep the URL.

    For /rc/clk or /pagead URLs, retain the URL because
    it may be the only job URL Google exposes.
    """

    if not url:
        return ""

    url = html.unescape(
        url.strip()
    )

    parsed = urlparse(url)

    # Remove Google/Indeed fragments.
    parsed = parsed._replace(
        fragment=""
    )

    return parsed.geturl()


# ============================================================
# INDEED TITLE / LOCATION / COMPANY
# ============================================================

def clean_indeed_title(title):
    """
    Clean Google's title for an Indeed result.
    """

    title = " ".join(
        title.split()
    ).strip()

    if not title:
        return ""

    # Remove standard Indeed suffix.
    title = re.sub(
        r"\s+-\s+Indeed(?:\.com)?$",
        "",
        title,
        flags=re.IGNORECASE,
    )

    # Remove common location suffixes.
    location_suffix = (
        r"(?:Toronto|North York|Scarborough|Etobicoke|"
        r"Markham|Richmond Hill|Vaughan|Thornhill|"
        r"Mississauga|Brampton|Oakville|Ajax|"
        r"Pickering|Whitby|Oshawa|Greater Toronto Area)"
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
    Infer the location from Google's actual result content.

    The search query itself is NOT used as evidence for location.
    """

    normalized = " ".join(
        text.lower().split()
    )

    location_patterns = [
        (
            "greater toronto area",
            "Greater Toronto Area, ON",
        ),
        (
            "north york",
            "North York, ON",
        ),
        (
            "scarborough",
            "Scarborough, ON",
        ),
        (
            "etobicoke",
            "Etobicoke, ON",
        ),
        (
            "markham",
            "Markham, ON",
        ),
        (
            "richmond hill",
            "Richmond Hill, ON",
        ),
        (
            "vaughan",
            "Vaughan, ON",
        ),
        (
            "thornhill",
            "Thornhill, ON",
        ),
        (
            "mississauga",
            "Mississauga, ON",
        ),
        (
            "brampton",
            "Brampton, ON",
        ),
        (
            "oakville",
            "Oakville, ON",
        ),
        (
            "ajax",
            "Ajax, ON",
        ),
        (
            "pickering",
            "Pickering, ON",
        ),
        (
            "whitby",
            "Whitby, ON",
        ),
        (
            "oshawa",
            "Oshawa, ON",
        ),
        (
            "toronto",
            "Toronto, ON",
        ),
        (
            "gta",
            "GTA",
        ),
    ]

    for needle, location in location_patterns:
        if needle in normalized:
            return location

    remote_patterns = [
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
        for pattern in remote_patterns
    ):
        return "Remote, Canada"

    if (
        "remote" in normalized
        and "canada" in normalized
    ):
        return "Remote, Canada"

    return ""


def extract_indeed_company(snippet):
    """
    Best-effort company extraction from Google's snippet.

    We do not open Indeed because direct Indeed requests from
    GitHub Actions were returning HTTP 403.
    """

    if not snippet:
        return "Unknown"

    text = " ".join(
        snippet.split()
    ).strip()

    # Common pattern:
    #
    # Company Name 4.2 4.2 out of 5 stars ...
    #
    match = re.match(
        r"^(.{2,100}?)\s+"
        r"\d(?:\.\d)?"
        r"(?:\s+\d(?:\.\d)?)?"
        r"(?:\s+out of 5 stars?)?",
        text,
        flags=re.IGNORECASE,
    )

    if match:
        company = match.group(
            1
        ).strip(
            " -|·"
        )

        if company:
            return company

    # Another common pattern:
    #
    # Company Name · 4.2 · Toronto, ON
    #
    parts = [
        part.strip(
            " -|"
        )
        for part in re.split(
            r"\s+[·•]\s+",
            text,
        )
        if part.strip()
    ]

    if parts:
        first = parts[0]

        if (
            first.lower()
            not in {
                "indeed",
                "indeed.com",
            }
            and len(first) <= 100
            and not re.search(
                r"\bremote\b|\bcanada\b",
                first,
                re.IGNORECASE,
            )
        ):
            return first

    return "Unknown"


# ============================================================
# GOOGLE DIAGNOSTICS
# ============================================================

def print_google_diagnostics(
    search_name,
    query,
    response,
    soup,
):
    """
    Print enough information to diagnose what Google actually
    returned to GitHub Actions.

    This is intentionally verbose while we stabilize the
    Google -> Indeed discovery.
    """

    print(
        ""
    )

    print(
        "---------------- GOOGLE DIAGNOSTICS ----------------"
    )

    print(
        f"Google {search_name} query:"
    )

    print(
        f"  {query}"
    )

    print(
        f"Google {search_name} HTTP status:"
        f" {response.status_code}"
    )

    print(
        f"Google {search_name} final URL:"
        f" {response.url}"
    )

    print(
        f"Google {search_name} response bytes:"
        f" {len(response.content)}"
    )

    page_title = ""

    if soup.title:
        page_title = soup.title.get_text(
            " ",
            strip=True,
        )

    print(
        f"Google {search_name} page title:"
        f" {page_title}"
    )

    body_text = soup.get_text(
        " ",
        strip=True,
    )

    body_lower = body_text.lower()

    print(
        f"Google {search_name} raw 'indeed.com' count:"
        f" {body_lower.count('indeed.com')}"
    )

    print(
        f"Google {search_name} raw '/viewjob' count:"
        f" {body_lower.count('/viewjob')}"
    )

    print(
        f"Google {search_name} raw 'Scrum Master' count:"
        f" {body_lower.count('scrum master')}"
    )

    if (
        "consent.google.com" in response.url
        or "before you continue" in body_lower
        or "unusual traffic" in body_lower
        or "captcha" in body_lower
    ):
        print(
            f"Google {search_name} WARNING:"
            " response may be a consent/challenge page."
        )

    indeed_anchors = []

    for anchor in soup.select(
        "a[href]"
    ):
        raw_href = anchor.get(
            "href",
            "",
        )

        extracted = extract_google_result_url(
            raw_href
        )

        if is_indeed_domain_url(
            extracted
        ):
            text = anchor.get_text(
                " ",
                strip=True,
            )

            indeed_anchors.append(
                {
                    "text": text,
                    "href": extracted,
                }
            )

    print(
        f"Google {search_name} Indeed-domain anchors:"
        f" {len(indeed_anchors)}"
    )

    if indeed_anchors:
        print(
            f"Google {search_name} Indeed anchor samples:"
        )

        for item in indeed_anchors[:10]:
            print(
                "  TEXT: "
                f"{item['text'][:160]}"
            )

            print(
                "  URL:  "
                f"{item['href'][:500]}"
            )
    else:
        print(
            f"Google {search_name}:"
            " no Indeed-domain anchors were found."
        )

        # Show a few Google result anchors so we can see what
        # Google is actually returning.
        print(
            f"Google {search_name} non-Indeed anchor samples:"
        )

        shown = 0

        for anchor in soup.select(
            "a[href]"
        ):
            raw_href = anchor.get(
                "href",
                "",
            )

            if not raw_href:
                continue

            extracted = extract_google_result_url(
                raw_href
            )

            text = anchor.get_text(
                " ",
                strip=True,
            )

            if not text:
                continue

            if (
                "google.com" in extracted.lower()
                and "/search" in extracted.lower()
            ):
                continue

            print(
                "  TEXT: "
                f"{text[:160]}"
            )

            print(
                "  URL:  "
                f"{extracted[:500]}"
            )

            shown += 1

            if shown >= 10:
                break

    print(
        "----------------------------------------------------"
    )

    print(
        ""
    )


# ============================================================
# GOOGLE -> INDEED
# ============================================================

def fetch_google_indeed(
    search_name,
    query,
):
    """
    Search Google for Indeed job pages.

    Google is used only as a free discovery mechanism.

    No Indeed API is used.

    No paid search API is used.

    Google's qdr:d filter asks for approximately the past day.
    """

    print(
        f"Searching Indeed through Google: "
        f"{search_name}"
    )

    params = {
        "q": query,
        "hl": "en",
        "gl": "ca",
        "num": GOOGLE_SEARCH_RESULTS,
        "filter": "0",
        "tbs": "qdr:d",
        "gbv": "1",
    }

    try:
        response = requests.get(
            GOOGLE_SEARCH_URL,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # ----------------------------------------------------
        # IMPORTANT DIAGNOSTIC OUTPUT
        # ----------------------------------------------------

        print_google_diagnostics(
            search_name,
            query,
            response,
            soup,
        )

        # ----------------------------------------------------
        # Extract Google results.
        #
        # Do not depend on one Google CSS class because Google
        # frequently changes its markup.
        # ----------------------------------------------------

        results = []

        # First try standard Google result blocks.
        result_blocks = soup.select(
            "div.MjjYud"
        )

        for block in result_blocks:
            anchors = block.select(
                "a[href]"
            )

            for anchor in anchors:
                raw_href = anchor.get(
                    "href",
                    "",
                )

                link = extract_google_result_url(
                    raw_href
                )

                if not is_indeed_job_url(
                    link
                ):
                    continue

                title_el = block.select_one(
                    "h3"
                )

                title = ""

                if title_el:
                    title = title_el.get_text(
                        " ",
                        strip=True,
                    )

                if not title:
                    title = anchor.get_text(
                        " ",
                        strip=True,
                    )

                snippet_el = block.select_one(
                    ".VwiC3b"
                )

                if not snippet_el:
                    snippet_el = block.select_one(
                        ".yXK7lf"
                    )

                if not snippet_el:
                    snippet_el = block.select_one(
                        "div[data-sncf]"
                    )

                if snippet_el:
                    snippet = snippet_el.get_text(
                        " ",
                        strip=True,
                    )
                else:
                    snippet = block.get_text(
                        " ",
                        strip=True,
                    )

                results.append(
                    {
                        "title": title,
                        "snippet": snippet,
                        "link": link,
                    }
                )

        # ----------------------------------------------------
        # Second pass:
        #
        # Scan every anchor. This is the important fallback
        # for Google markup changes.
        # ----------------------------------------------------

        if not results:
            for anchor in soup.select(
                "a[href]"
            ):
                raw_href = anchor.get(
                    "href",
                    "",
                )

                link = extract_google_result_url(
                    raw_href
                )

                if not is_indeed_job_url(
                    link
                ):
                    continue

                title_el = anchor.select_one(
                    "h3"
                )

                if title_el:
                    title = title_el.get_text(
                        " ",
                        strip=True,
                    )
                else:
                    title = anchor.get_text(
                        " ",
                        strip=True,
                    )

                # Look around the anchor for useful snippet text.
                parent = anchor.parent

                if parent:
                    snippet = parent.get_text(
                        " ",
                        strip=True,
                    )
                else:
                    snippet = ""

                results.append(
                    {
                        "title": title,
                        "snippet": snippet,
                        "link": link,
                    }
                )

        # ----------------------------------------------------
        # Third pass:
        #
        # Some Google responses expose an Indeed URL in an
        # ancestor while the clickable anchor is elsewhere.
        # Scan the raw HTML for Indeed URLs.
        # ----------------------------------------------------

        if not results:
            raw_html = response.text

            patterns = [
                r'https?://(?:[a-z]{2,3}\.)?indeed\.com/viewjob\?[^"\'>\s]+',
                r'https?://(?:[a-z]{2,3}\.)?indeed\.com/rc/clk\?[^"\'>\s]+',
                r'https?://(?:[a-z]{2,3}\.)?indeed\.com/pagead/[^"\'>\s]+',
            ]

            raw_links = []

            for pattern in patterns:
                raw_links.extend(
                    re.findall(
                        pattern,
                        raw_html,
                        flags=re.IGNORECASE,
                    )
                )

            for raw_link in raw_links:
                link = html.unescape(
                    raw_link
                )

                link = unquote(
                    link
                )

                if not is_indeed_job_url(
                    link
                ):
                    continue

                results.append(
                    {
                        "title": "",
                        "snippet": "",
                        "link": link,
                    }
                )

        # ----------------------------------------------------
        # Deduplicate Google results.
        # ----------------------------------------------------

        unique_results = {}

        for result in results:
            link = result.get(
                "link",
                "",
            )

            if link:
                unique_results[
                    normalize_indeed_job_url(link)
                ] = result

        results = list(
            unique_results.values()
        )

        print(
            f"Google {search_name} "
            f"Indeed job links found: "
            f"{len(results)}"
        )

        # ----------------------------------------------------
        # Show the actual extracted results.
        # ----------------------------------------------------

        if results:
            print(
                f"Google {search_name} extracted Indeed results:"
            )

            for index, result in enumerate(
                results[:10],
                start=1,
            ):
                print(
                    f"  [{index}] "
                    f"Title: "
                    f"{result['title'][:200]}"
                )

                print(
                    f"      URL: "
                    f"{result['link'][:500]}"
                )

                print(
                    f"      Snippet: "
                    f"{result['snippet'][:300]}"
                )

        jobs = []

        # ----------------------------------------------------
        # Convert Google results into our standard job object.
        # ----------------------------------------------------

        for result in results:
            title = clean_indeed_title(
                result.get(
                    "title",
                    "",
                )
            )

            snippet = result.get(
                "snippet",
                "",
            ).strip()

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
                "link": normalize_indeed_job_url(
                    result["link"]
                ),
                "source": "Indeed",
            }

            # ------------------------------------------------
            # Diagnostic rejection logging.
            # ------------------------------------------------

            if is_relevant(job):
                jobs.append(
                    job
                )

                print(
                    "  ACCEPTED Indeed job:"
                    f" {title} | "
                    f"{company} | "
                    f"{location}"
                )

            else:
                print(
                    "  REJECTED Indeed result:"
                    f" title='{title}' | "
                    f"location='{location}' | "
                    f"url='{result['link'][:200]}'"
                )

        print(
            f"Google {search_name} "
            f"relevant Indeed jobs: "
            f"{len(jobs)}"
        )

        return jobs

    except requests.RequestException as error:
        print(
            f"Google {search_name} "
            f"request error: "
            f"{error}"
        )

        return []

    except Exception as error:
        print(
            f"Google {search_name} "
            f"error: "
            f"{error}"
        )

        return []


# ============================================================
# INDEED
# ============================================================

def fetch_indeed():
    """
    Discover Indeed jobs through Google.

    Toronto:
        Scrum Master + Toronto/GTA terms

    Remote:
        Scrum Master + Remote + Canada

    Google is asked for the past day.

    We intentionally do not request Indeed directly because
    Indeed was returning HTTP 403 from GitHub Actions.
    """

    toronto_locations_query = (
        '("Toronto" OR '
        '"Greater Toronto Area" OR '
        '"North York" OR '
        '"Scarborough" OR '
        '"Etobicoke" OR '
        '"Markham" OR '
        '"Richmond Hill" OR '
        '"Vaughan" OR '
        '"Thornhill" OR '
        '"Mississauga" OR '
        '"Brampton" OR '
        '"Oakville" OR '
        '"Ajax" OR '
        '"Pickering" OR '
        '"Whitby" OR '
        '"Oshawa" OR '
        '"GTA")'
    )

    searches = [
        (
            "Toronto",
            (
                'site:ca.indeed.com '
                '"Scrum Master" '
                f"{toronto_locations_query}"
            ),
        ),
        (
            "Remote Canada",
            (
                'site:ca.indeed.com '
                '"Scrum Master" '
                '("remote" OR '
                '"work from home") '
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
        unique_jobs[
            job_id(job)
        ] = job

    jobs = list(
        unique_jobs.values()
    )

    print(
        f"Indeed Scrum Master jobs "
        f"(via Google): "
        f"{len(jobs)}"
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
            f"Searching LinkedIn: "
            f"{search_name}"
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

            cards = soup.select(
                "li"
            )

            print(
                f"LinkedIn {search_name} "
                f"cards found: "
                f"{len(cards)}"
            )

            relevant_count = 0

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

                    if (
                        not title_el
                        or not link_el
                    ):
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
                        jobs.append(
                            job
                        )

                        relevant_count += 1

                except Exception as error:
                    print(
                        f"LinkedIn card error: "
                        f"{error}"
                    )

                    continue

            print(
                f"LinkedIn {search_name} "
                f"relevant Scrum Master jobs: "
                f"{relevant_count}"
            )

        except requests.RequestException as error:
            print(
                f"LinkedIn {search_name} "
                f"request error: "
                f"{error}"
            )

        except Exception as error:
            print(
                f"LinkedIn {search_name} "
                f"error: "
                f"{error}"
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

            data = json.load(
                file
            )

        if isinstance(
            data,
            dict,
        ):
            return data

        print(
            "seen.json does not contain "
            "a JSON object."
        )

        return {}

    except Exception as error:

        print(
            f"Could not load "
            f"{SEEN_FILE}: "
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

    if (
        not email_user
        or not email_pass
        or not email_to
    ):

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
            f"Email error: "
            f"{error}"
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

        if not result.get(
            "ok"
        ):
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
            f"Telegram error: "
            f"{error}"
        )

        return False


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 60
    )

    print(
        "Scrum Master Job Alerts"
    )

    print(
        "=" * 60
    )

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

        key = job_id(
            job
        )

        if key not in seen:
            new_jobs.append(
                job
            )

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

    print(
        "New jobs:"
    )

    for job in new_jobs:

        print(
            f"  - {job['title']} | "
            f"{job['company']} | "
            f"{job['location']} | "
            f"{job['source']}"
        )

        print(
            f"    {job['link']}"
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
    # IMPORTANT:
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
        f"{len(new_jobs)} new jobs "
        f"to seen.json."
    )


if __name__ == "__main__":
    main()
