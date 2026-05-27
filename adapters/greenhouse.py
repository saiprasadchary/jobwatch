import re
import requests

API_BASE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

_SALARY_RE = re.compile(r"\$[\d,]+(?:\.\d+)?(?:\s*[-–—to]+\s*\$[\d,]+(?:\.\d+)?)?(?:\s*/\s*(?:yr|year|hr|hour|annually))?", re.IGNORECASE)


def _extract_salary(content: str) -> str | None:
    if not content:
        return None
    m = _SALARY_RE.search(content)
    return m.group(0).strip() if m else None


def fetch_greenhouse(company: dict) -> list[dict]:
    slug = company["slug"]
    url = API_BASE.format(slug=slug)
    resp = requests.get(url, params={"content": "true"}, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for j in data.get("jobs", []):
        location = j.get("location", {}).get("name", "")
        content = j.get("content", "")
        jobs.append({
            "job_id": f"gh-{slug}-{j['id']}",
            "company": company["name"],
            "title": j.get("title", ""),
            "location": location,
            "url": j.get("absolute_url", ""),
            "posted_at": j.get("updated_at") or j.get("created_at"),
            "salary": _extract_salary(content),
        })
    return jobs
