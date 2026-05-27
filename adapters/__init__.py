from .greenhouse import fetch_greenhouse
from .lever import fetch_lever
from .ashby import fetch_ashby
from .smartrecruiters import fetch_smartrecruiters
from .workday import fetch_workday
from .talentbrew import fetch_talentbrew
from .oleeo import fetch_oleeo
from .eightfold import fetch_eightfold
from .hn_hiring import fetch_hn_hiring
from .phenom import fetch_phenom
from .amazon import fetch_amazon
from .playwright_scraper import fetch_playwright

ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "talentbrew": fetch_talentbrew,
    "oleeo": fetch_oleeo,
    "eightfold": fetch_eightfold,
    "hn_hiring": fetch_hn_hiring,
    "phenom": fetch_phenom,
    "amazon": fetch_amazon,
    "playwright": fetch_playwright,
}
