"""Rank same-host contact, biography and social pages in DE/FR/IT.

Only navigation links already parsed from the source page are considered.
The caller remains responsible for HTTPS and same-host validation.
"""
import unicodedata
from urllib.parse import urlparse


_TERMS = {
    "kontakt", "contact", "contatto", "contatti", "contacter", "kontakte",
    "about", "about-me", "ueber-mich", "uber-mich", "über-mich",
    "a-propos", "à-propos", "a propos", "chi-sono", "chi sono",
    "social-media", "socialmedia", "reseaux-sociaux", "réseaux sociaux",
    "reti-sociali", "social", "links", "liens", "collegamenti",
    "person", "personen", "profil", "profile", "profils", "profili",
    "biografie", "biography", "biographie", "biografia", "bio",
    "politique", "politik", "politica",
}


def _normalize(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def rank_internal_pages(current, navigation_links, *, limit=2):
    """Return at most limit unique HTTPS same-host pages, strongest first."""
    origin = urlparse(current)
    ranked = {}
    rejected = 0
    terms = {_normalize(term).replace(" ", "-") for term in _TERMS}
    from urllib.parse import urljoin
    for entry in navigation_links:
        try:
            target = urljoin(current, entry["href"])
            parsed = urlparse(target)
            path = _normalize(parsed.path.rstrip("/"))
            label = _normalize(entry.get("text", "")).strip().replace(" ", "-")
            if (parsed.scheme != "https" or parsed.hostname != origin.hostname
                    or parsed.port not in (None, 443) or parsed.username
                    or parsed.password or parsed.query or parsed.fragment
                    or target == current):
                rejected += 1
                continue
            path_match = any(path.endswith("/" + term) for term in terms)
            label_match = any(term in label.split("-") or label == term for term in terms)
            if not (path_match or label_match):
                rejected += 1
                continue
            score = 2 * path_match + label_match
            ranked[target] = max(score, ranked.get(target, 0))
        except (KeyError, TypeError, ValueError, UnicodeError):
            rejected += 1
    ordered = sorted(ranked, key=lambda url: (-ranked[url], url))
    return ordered[:limit], len(ordered), rejected
