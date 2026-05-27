import re
from datetime import datetime, timezone, timedelta

NEGATIVE_TITLE_PATTERNS = [
    r"\bintern\b",
    r"\binternship\b",
    r"\bco-op\b",
    r"\bmanager\b",
    r"\bmanagement\b",
    r"\bdirector\b",
    r"\bvice president\b",
    r"\b(?:sr\.?\s*)?vp\b",
    r"\bchief\b",
    r"\bcto\b",
    r"\bcio\b",
    r"\bceo\b",
    r"\bhead of\b",
    r"\b(?:technical |tech )?lead\b(?!\s+(?:software|engineer|developer))",
    r"\bprincipal\b",
    r"\bclearance required\b",
    r"\bts/sci\b",
    r"\bsecurity clearance\b",
    r"\bpolygraph\b",
]

_neg_re = re.compile("|".join(NEGATIVE_TITLE_PATTERNS), re.IGNORECASE)

# ── STRICT US-ONLY ALLOWLIST ─────────────────────────────────────────
# If a location has no clear US signal, it is dropped. No non-US blocklist.

# Two-letter US state abbreviations (all 50 + DC + territories).
# Note: "IN" (Indiana) excluded — too ambiguous with India/IN-KA codes.
# Indiana is caught by full state name "Indiana" and cities like Indianapolis.
_US_STATES = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR", "GU", "VI",
)

# Full state names.
_US_STATE_NAMES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
)

# Comprehensive US cities list (150+).
# Top 100 by population + major tech hubs + financial centers.
_US_CITIES = (
    # Top US cities by population
    "New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia",
    "San Antonio", "San Diego", "Dallas", "San Jose", "Austin", "Jacksonville",
    "Fort Worth", "Columbus", "Indianapolis", "Charlotte", "San Francisco",
    "Seattle", "Denver", "Nashville", "Oklahoma City", "El Paso", "Memphis",
    "Louisville", "Milwaukee", "Tucson", "Albuquerque", "Fresno", "Sacramento",
    "Mesa", "Kansas City", "Atlanta", "Omaha", "Raleigh", "Minneapolis",
    "Cleveland", "Tampa", "Aurora", "New Orleans", "Pittsburgh", "St. Louis",
    "Cincinnati", "Orlando", "Irvine", "Baltimore", "Bakersfield",
    "Riverside", "Stockton", "Corpus Christi", "Lexington", "Henderson",
    "St. Paul", "Anchorage", "Newark", "Buffalo", "Honolulu",
    "Greensboro", "Lincoln", "Norfolk", "Chesapeake", "Chula Vista",
    "Chandler", "Scottsdale", "Laredo", "Lubbock", "Gilbert",
    "Winston-Salem", "Glendale", "Hialeah", "Garland", "Irving",
    "North Las Vegas", "Fremont", "Richmond", "Baton Rouge",
    "Des Moines", "Spokane", "Birmingham", "Rochester", "Modesto",
    "Oxnard", "Tacoma", "Fontana", "Moreno Valley", "Fayetteville",
    "Huntsville", "Yonkers", "Glendale", "Knoxville", "Worcester",
    "Providence", "Brownsville", "Little Rock", "Dayton", "Akron",
    "Tallahassee", "Mobile", "Grand Rapids", "Overland Park",
    "Sioux Falls", "Chattanooga", "Fort Lauderdale",
    "Springfield", "Savannah", "Clarksville",
    "Las Vegas", "Virginia Beach", "Long Beach", "Oakland",
    "Miami", "Tulsa", "Colorado Springs", "Wichita",
    "St. Petersburg",
    # Major tech hubs
    "Sunnyvale", "Santa Clara", "Mountain View", "Palo Alto", "Menlo Park",
    "Cupertino", "Redmond", "Bellevue", "Kirkland", "Boulder",
    "Reston", "Herndon", "McLean", "Tysons", "Tysons Corner",
    "Cary", "Durham", "Research Triangle", "Tempe",
    "Burlington", "Waltham", "Cambridge, MA",
    "Ann Arbor", "Detroit", "Portland", "Boise", "Madison",
    "Plano", "Provo", "Salt Lake City",
    "Redwood City", "San Mateo", "Foster City", "Milpitas",
    "Santa Monica", "Venice Beach", "Pasadena", "Burbank",
    "Thousand Oaks", "Calabasas", "Culver City",
    "Schaumburg", "Naperville", "Evanston",
    "Alpharetta", "Sandy Springs", "Roswell",
    "Plano", "Frisco", "Richardson", "Allen", "McKinney",
    "Round Rock", "Cedar Park", "Georgetown",
    "Bellevue", "Bothell", "Issaquah",
    "Lehi", "Draper", "Orem",
    "Raleigh-Durham", "Chapel Hill", "Wake Forest",
    "Ashburn", "Sterling", "Chantilly", "Fairfax", "Manassas",
    "Ellicott City",
    "Troy", "Dearborn", "Southfield",
    "Bloomington", "Eden Prairie", "Minnetonka",
    "King of Prussia", "Conshohocken", "Blue Bell",
    # Financial centers
    "Greenwich", "Stamford", "Hoboken", "Jersey City", "White Plains",
    "Wilmington",
    # Additional notable cities
    "Arlington", "Boston", "San Bernardino",
    "Carlsbad", "Oceanside", "Escondido",
    "Peoria", "Surprise", "Avondale",
    "Reno", "Sparks", "Henderson",
    "Charleston", "Greenville",
    "Pensacola", "Gainesville", "Lakeland",
    "Fort Collins", "Arvada", "Westminster",
)

# Non-US qualifiers that disqualify "Remote" / "Hybrid" / "Anywhere".
_NON_US_QUALIFIERS = (
    "india", "uk", "europe", "canada", "apac", "emea", "latam",
    "british columbia", "ontario", "quebec", "alberta", "manitoba",
    "saskatchewan", "nova scotia", "new brunswick",
    "global", "worldwide", "international",
)
_NON_US_QUAL_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(q) for q in _NON_US_QUALIFIERS) + r")\b",
    re.IGNORECASE,
)

# Country-code prefix pattern: two uppercase letters followed by a dash
# at the start of the location string (e.g. "CA-Remote-British Columbia",
# "IN-Bangalore", "DE-Berlin").  US- prefix is excluded.
_COUNTRY_CODE_PREFIX_RE = re.compile(r"^(?!US-)[A-Z]{2}-")

# Build the main US-positive regex.
_us_re = re.compile(
    # US synonyms (word-boundary protected)
    # "U.S." and "U.S.A." need special handling: \b doesn't work after "."
    r"\b(?:usa|united\s+states)\b"
    r"|\bu\.s\.a\.(?:\b|$|(?=[\s,;)/-]))"
    r"|\bu\.s\.(?:\b|$|(?=[\s,;)/-]))"
    # "US" with strict word boundaries — must not be inside words like
    # "campus", "focus", "status", "versus", "us" (pronoun at sentence end).
    # Require US to be preceded by start-of-string, space, comma, or paren
    # and followed by end-of-string, space, comma, paren, or dash.
    r"|(?:^|(?<=[\s,(]))US(?=$|[\s,)/-])"
    # "America" (but not "Latin America", "South America", "Central America")
    # Variable-width lookbehinds not supported, so use three fixed-width ones.
    r"|(?<!latin )(?<!south )(?<!central )\bamerica\b"
    # State abbreviations after ", " (e.g. ", CA", ", NY")
    # Must NOT be followed by "-" to avoid matching ", IN-KA" as Indiana.
    r"|,\s*(?:" + "|".join(re.escape(s) for s in _US_STATES) + r")(?=[,\s);/]|$)"
    # State abbreviations after whitespace at end of string
    # (e.g. "Dallas TX").  Also must not be followed by "-".
    r"|\s(?:" + "|".join(re.escape(s) for s in _US_STATES) + r")$"
    # Full state names
    r"|\b(?:" + "|".join(re.escape(n) for n in _US_STATE_NAMES) + r")\b"
    # US cities
    r"|\b(?:" + "|".join(re.escape(c) for c in _US_CITIES) + r")\b"
    # "Columbia" is ambiguous (Columbia SC/MD vs British Columbia).
    # Only match when NOT preceded by "British " (case-insensitive via
    # two fixed-width lookbehinds).
    r"|(?<!british )(?<!British )\bcolumbia\b",
    re.IGNORECASE,
)

# Separate regex for "Remote" / "Hybrid" / "Anywhere" — these are US-positive
# ONLY if they don't have non-US qualifiers nearby and don't have a
# country-code prefix.
_REMOTE_RE = re.compile(
    r"\b(?:remote|hybrid)\b",
    re.IGNORECASE,
)


def matches_keywords(title: str, keywords: list[str]) -> bool:
    title_lower = title.lower()
    for kw in keywords:
        if kw.lower() in title_lower:
            return True
    return False


def is_excluded(title: str) -> bool:
    return bool(_neg_re.search(title))


def is_non_us(location: str) -> bool:
    """Strict US-only allowlist.

    Returns True (= drop the job) unless there is a clear US signal.
    - Empty/missing location → KEEP (benefit of the doubt).
    - Explicit US signal (state, city, country name) → KEEP.
    - "Remote"/"Hybrid"/"Anywhere" without non-US qualifier → KEEP.
    - Everything else → DROP.
    """
    if not location or not location.strip():
        return False

    loc = location.strip()

    # 0. Country-code prefix (e.g. "CA-Remote", "IN-Bangalore", "DE-Berlin").
    #    If the location starts with a 2-letter non-US code + dash, it is
    #    non-US even if the code happens to match a US state abbreviation
    #    (e.g. "CA" = Canada here, not California).
    has_country_prefix = bool(_COUNTRY_CODE_PREFIX_RE.search(loc))
    if has_country_prefix:
        return True

    # 1. Check for explicit US signals (cities, states, country synonyms).
    if _us_re.search(loc):
        return False

    # 2. Check for "Remote" / "Hybrid".
    #    US-positive ONLY if:
    #    a) standalone ("Remote", "Hybrid") — no other meaningful content, OR
    #    b) paired with US signal that step 1 already found (which returned False).
    #    If Remote/Hybrid appears alongside non-US qualifiers or unrecognized
    #    content (e.g. "Hybrid - London"), it is NOT US-positive.
    if _REMOTE_RE.search(loc):
        # Strip out the remote/hybrid word and separators; if nothing
        # meaningful remains, it's standalone → US-positive.
        stripped = _REMOTE_RE.sub("", loc).strip(" -–—/|,;()")
        if not stripped:
            return False

    # 3. No US signal found → treat as non-US.
    return True


def matches_location(location: str, locations: list[str]) -> bool:
    if not locations:
        return True
    loc_lower = location.lower()
    for loc in locations:
        if loc.lower() in loc_lower:
            return True
    return False


MAX_AGE_HOURS = 24

_RELATIVE_RE = re.compile(r"Posted\s+(\d+)(?:\+)?\s+(day|hour|minute)s?\s+ago", re.IGNORECASE)


def _parse_timestamp(raw) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=timezone.utc)
        return raw
    s = str(raw).strip()

    if s.lower() in ("posted today", "today"):
        return datetime.now(timezone.utc)
    if s.lower() in ("posted yesterday", "yesterday"):
        return datetime.now(timezone.utc) - timedelta(days=1)
    m = _RELATIVE_RE.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = timedelta(**{f"{unit}s": n})
        return datetime.now(timezone.utc) - delta

    try:
        from datetime import datetime as dt_cls
        return dt_cls.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass

    for fmt in (
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def is_stale(job: dict, max_age_hours: int = MAX_AGE_HOURS) -> bool:
    posted = _parse_timestamp(job.get("posted_at"))
    if not posted:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    return posted < cutoff


def filter_jobs(
    jobs: list[dict],
    keywords: list[str],
    locations: list[str],
    exclude_non_us: bool = True,
) -> list[dict]:
    matched = []
    for job in jobs:
        title = job.get("title", "")
        location = job.get("location", "")

        if not matches_keywords(title, keywords):
            continue
        if is_excluded(title):
            continue
        if exclude_non_us and is_non_us(location):
            continue
        if not matches_location(location, locations):
            continue
        if is_stale(job):
            continue

        matched.append(job)
    return matched
