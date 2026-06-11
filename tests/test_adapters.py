"""Fixture-payload tests for the previously untested adapters.

job_id format stability is the load-bearing assertion in each test: job_id
is the dedup key, and a silent format change re-alerts every existing job
(the duplicate-alert bug the store layer was hardened against)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters import ashby, greenhouse, lever, netflix, oleeo, phenom, talentbrew, uber


class FakeResponse:
    def __init__(self, payload, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class GreenhouseTests(unittest.TestCase):
    def test_field_mapping_and_job_id(self) -> None:
        payload = {
            "jobs": [
                {
                    "id": 12345,
                    "title": "Backend Engineer",
                    "location": {"name": "Austin, TX"},
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/12345",
                    "updated_at": "2026-06-09T12:00:00Z",
                    "content": "Pay range: $150,000 - $200,000 annually",
                }
            ]
        }
        with patch.object(greenhouse.requests, "get", return_value=FakeResponse(payload)):
            jobs = greenhouse.fetch_greenhouse({"name": "Acme", "slug": "acme"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "gh-acme-12345")
        self.assertEqual(job["title"], "Backend Engineer")
        self.assertEqual(job["location"], "Austin, TX")
        self.assertEqual(job["posted_at"], "2026-06-09T12:00:00Z")
        self.assertIn("$150,000", job["salary"])


class LeverTests(unittest.TestCase):
    def test_field_mapping_epoch_conversion_and_job_id(self) -> None:
        payload = [
            {
                "id": "ab-12",
                "text": "Fullstack Developer",
                "categories": {"location": "Seattle, WA", "commitment": "Full-time"},
                "hostedUrl": "https://jobs.lever.co/acme/ab-12",
                "createdAt": 1765300000000,
                "descriptionPlain": "no salary listed",
            }
        ]
        with patch.object(lever.requests, "get", return_value=FakeResponse(payload)):
            jobs = lever.fetch_lever({"name": "Acme", "slug": "acme"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "lv-acme-ab-12")
        self.assertEqual(job["location"], "Seattle, WA")
        # Epoch ms converted to Z-suffixed ISO (a safe format for is_stale).
        self.assertRegex(job["posted_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class AshbyTests(unittest.TestCase):
    def test_field_mapping_and_job_id(self) -> None:
        payload = {
            "jobs": [
                {
                    "id": "uuid-1",
                    "title": "Platform Engineer",
                    "location": "Remote",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/uuid-1",
                    "publishedAt": "2026-06-09T08:00:00Z",
                    "compensationTierSummary": "$170K - $210K",
                }
            ]
        }
        with patch.object(ashby.requests, "get", return_value=FakeResponse(payload)):
            jobs = ashby.fetch_ashby({"name": "Acme", "slug": "acme"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "ab-acme-uuid-1")
        self.assertEqual(job["salary"], "$170K - $210K")
        self.assertEqual(job["posted_at"], "2026-06-09T08:00:00Z")


class NetflixTests(unittest.TestCase):
    def test_field_mapping_and_job_id(self) -> None:
        payload = {
            "positions": [
                {
                    "id": "790300000000",
                    "name": "Software Engineer L4",
                    "location": "Los Gatos, CA",
                }
            ],
            "page_count": 1,
        }
        with patch.object(netflix.requests, "get", return_value=FakeResponse(payload)):
            jobs = netflix.fetch_netflix({"name": "Netflix"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "netflix-790300000000")
        self.assertEqual(job["url"], "https://explore.jobs.netflix.net/careers/790300000000")
        self.assertEqual(job["source"], "netflix")


class UberTests(unittest.TestCase):
    def test_field_mapping_nested_results_and_job_id(self) -> None:
        payload = {
            "data": {
                "results": [
                    {
                        "id": 140000,
                        "title": "Senior Software Engineer",
                        "location": {"name": "San Francisco, CA"},
                        "postedAt": "2026-06-09T10:00:00Z",
                    }
                ]
            }
        }
        with patch.object(uber.requests, "post", return_value=FakeResponse(payload)):
            jobs = uber.fetch_uber({"name": "Uber"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "uber-140000")
        self.assertEqual(job["location"], "San Francisco, CA")
        self.assertEqual(job["url"], "https://www.uber.com/global/en/careers/list/140000/")


class PhenomTests(unittest.TestCase):
    def test_field_mapping_and_job_id(self) -> None:
        page_one = {
            "jobs": [
                {
                    "data": {
                        "title": "Software Engineer II",
                        "city": "Santa Clara",
                        "state": "California",
                        "country": "United States",
                        "req_id": "JR100",
                        "slug": "jr100-software-engineer-ii",
                        "postedDate": "2026-06-09T00:00:00Z",
                        "description": "",
                    }
                }
            ]
        }
        responses = iter([FakeResponse(page_one), FakeResponse({"jobs": []})])
        with patch.object(phenom.requests, "get", side_effect=lambda *a, **k: next(responses)):
            jobs = phenom.fetch_phenom({"name": "AMD", "domain": "careers.amd.com"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "ph-careers-JR100")
        self.assertEqual(job["location"], "Santa Clara, California, United States")
        self.assertIn("jr100-software-engineer-ii", job["url"])


class OleeoTests(unittest.TestCase):
    def test_html_row_parsing_and_job_id(self) -> None:
        html = (
            "<table><tr>"
            '<td><a href="https://ms.tal.net/vx/candidate/jobboard/vacancy/55501/detail">'
            "Software Engineer</a></td>"
            "<td><span>New York, NY</span></td>"
            "</tr></table>"
        )
        fake = FakeResponse({}, text=html)
        with patch.object(oleeo.requests, "get", return_value=fake):
            jobs = oleeo.fetch_oleeo({"name": "Morgan Stanley", "subdomain": "ms"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "ol-ms-55501")
        self.assertEqual(job["title"], "Software Engineer")
        self.assertEqual(job["location"], "New York, NY")


class TalentBrewTests(unittest.TestCase):
    def test_html_results_parsing_and_job_id(self) -> None:
        payload = {
            "results": (
                '<a href="/job/austin/software-engineer/123/456">Software Engineer</a>'
            )
        }
        with patch.object(talentbrew.requests, "get", return_value=FakeResponse(payload)):
            jobs = talentbrew.fetch_talentbrew({"name": "Arm", "domain": "careers.arm.com"})

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "tb-careers.arm.com-/job/austin/software-engineer/123/456")
        self.assertEqual(job["location"], "Austin")
        self.assertEqual(job["url"], "https://careers.arm.com/job/austin/software-engineer/123/456")

    def test_page_cap_terminates_runaway_pagination(self) -> None:
        # 100 anchors per page makes the loop ask for the next page forever
        # if the cap regresses.
        full_page = {
            "results": "".join(
                f'<a href="/job/loc/title-{i}/1/{i}">Job {i}</a>' for i in range(100)
            )
        }
        calls = {"n": 0}

        def fake_get(*_args, **_kwargs):
            calls["n"] += 1
            return FakeResponse(full_page)

        with patch.object(talentbrew.requests, "get", side_effect=fake_get):
            talentbrew.fetch_talentbrew({"name": "Arm", "domain": "careers.arm.com"})

        self.assertEqual(calls["n"], talentbrew.MAX_PAGES)


if __name__ == "__main__":
    unittest.main()
