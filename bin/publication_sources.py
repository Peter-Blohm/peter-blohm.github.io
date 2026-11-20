"""Public author catalogues and their normalization into publication records."""

import json
import os
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from scholar_profile import fetch_scholar_publications, load_scholar_user_id


@dataclass(frozen=True)
class Work:
    title: str
    authors: tuple[str, ...]
    year: str
    source: str
    kind: str = ""
    venue: str = ""
    url: str = ""
    doi: str = ""
    arxiv: str = ""
    status: str = ""
    group: str = ""
    affiliations: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    volume: str = ""
    pages: str = ""
    number: str = ""
    publisher: str = ""


def get_json(base, path, params, headers):
    request = Request(
        f"{base}/{path}?{urlencode(params)}",
        headers={"Accept": "application/json", **headers},
    )
    for retry in range(3):
        try:
            with urlopen(request, timeout=15) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or retry == 2:
                raise
        except (URLError, TimeoutError):
            if retry == 2:
                raise
        time.sleep(2**retry)


def google_scholar(known_titles, title_key):
    from scholarly import scholarly

    failures = 0
    for record in fetch_scholar_publications(load_scholar_user_id()):
        bib = record.get("bib", {})
        title = bib.get("title", "")
        if title_key(title) in known_titles:
            continue
        try:
            filled = scholarly.fill(record)
        except Exception:
            failures += 1
            continue
        details = filled.get("bib", {})
        venue = details.get("conference") or details.get("journal") or ""
        yield Work(
            details.get("title") or title,
            tuple(details.get("author", "").split(" and ")),
            str(details.get("pub_year") or ""),
            "Google Scholar",
            kind=(
                "conference"
                if details.get("conference")
                else "article" if venue else ""
            ),
            venue=venue,
            url=filled.get("pub_url") or "",
            doi=details.get("doi") or "",
            status=bib.get("citation", ""),
            volume=str(details.get("volume") or ""),
            pages=str(details.get("pages") or ""),
            number=str(details.get("number") or ""),
            publisher=details.get("publisher") or "",
        )
    if failures:
        raise ScholarDetailsUnavailable


class ScholarDetailsUnavailable(Exception):
    """Scholar did not provide details for some profile entries."""


def openalex(config, title_key, author_matches):
    key = os.getenv("OPENALEX_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    def get(path, **params):
        return get_json("https://api.openalex.org", path, params, headers)

    def papers(author):
        cursor = "*"
        results = []
        for _ in range(20):
            response = get(
                "works",
                filter=f"authorships.author.id:{author}",
                per_page=100,
                cursor=cursor,
            )
            results.extend(response["results"])
            cursor = response.get("meta", {}).get("next_cursor")
            if not cursor or not response["results"]:
                return results
        raise ValueError("OpenAlex pagination limit reached")

    author_id = config.get("openalex_author_id")
    if author_id:
        records = papers(author_id)
        verify_profile(records, config, title_key, lambda item: item.get("title"))
    else:
        profiles = get("authors", search=config["author"], per_page=10)["results"]
        records = resolve_profile(
            (
                (profile["id"].rsplit("/", 1)[-1], profile.get("display_name", ""))
                for profile in profiles
            ),
            papers,
            config,
            title_key,
            lambda item: item.get("title"),
        )

    for item in records:
        location = item.get("primary_location") or {}
        venue = (location.get("source") or {}).get("display_name") or ""
        # Preprints can be a secondary location of the published work.
        arxiv = next(
            (
                loc["landing_page_url"]
                for loc in item.get("locations", [])
                if (loc.get("landing_page_url") or "").startswith("https://arxiv.org/")
            ),
            "",
        )
        authorships = item.get("authorships", [])
        affiliations = tuple(
            institution.get("display_name") or ""
            for authorship in authorships
            if author_matches(
                authorship["author"].get("display_name") or "", config["author"]
            )
            for institution in authorship.get("institutions", [])
        )
        topic = item.get("primary_topic") or {}
        field = (topic.get("field") or {}).get("display_name") or ""
        yield Work(
            item.get("title") or "",
            tuple(auth["author"].get("display_name") or "" for auth in authorships),
            str(item.get("publication_year") or ""),
            "OpenAlex",
            kind=item.get("type") or "",
            venue=venue,
            url=location.get("landing_page_url") or item.get("doi") or "",
            doi=item.get("doi") or "",
            arxiv=arxiv,
            status="retracted" if item.get("is_retracted") else "",
            affiliations=affiliations,
            fields=(field,),
        )


def semantic_scholar(config, title_key):
    key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    headers = {"x-api-key": key} if key else {}

    def get(path, **params):
        return get_json(
            "https://api.semanticscholar.org/graph/v1", path, params, headers
        )

    def papers(author):
        offset = 0
        results = []
        for _ in range(20):
            response = get(
                f"author/{author}/papers",
                limit=100,
                offset=offset,
                fields="title,authors,year,venue,publicationVenue,publicationTypes,externalIds,fieldsOfStudy",
            )
            results.extend(response["data"])
            offset = response.get("next")
            if offset is None:
                return results
        raise ValueError("Semantic Scholar pagination limit reached")

    author_id = config.get("semantic_scholar_author_id")
    if author_id:
        records = papers(author_id)
        verify_profile(records, config, title_key, lambda item: item.get("title"))
    else:
        profiles = get(
            "author/search", query=config["author"], limit=10, fields="name"
        )["data"]
        records = resolve_profile(
            ((profile["authorId"], profile.get("name", "")) for profile in profiles),
            papers,
            config,
            title_key,
            lambda item: item.get("title"),
        )

    for item in records:
        ids = item.get("externalIds") or {}
        venue = item.get("publicationVenue") or {}
        doi = ids.get("DOI") or ""
        arxiv = ids.get("ArXiv") or ""
        types = item.get("publicationTypes") or []
        kind = next(
            (
                kind
                for kind in ("JournalArticle", "Conference", "Book", "BookSection")
                if kind in types
            ),
            "",
        )
        yield Work(
            item.get("title") or "",
            tuple(auth.get("name") or "" for auth in item.get("authors", [])),
            str(item.get("year") or ""),
            "Semantic Scholar",
            kind=kind,
            venue=venue.get("name") or item.get("venue") or "",
            url=(
                f"https://doi.org/{doi}"
                if doi
                else f"https://arxiv.org/abs/{arxiv}" if arxiv else ""
            ),
            doi=doi,
            arxiv=arxiv,
            status="retracted" if "Retraction" in types else "",
            fields=tuple(item.get("fieldsOfStudy") or ()),
        )


def verify_profile(records, config, title_key, get_title):
    anchors = {title_key(title) for title in config["identity_papers"]}
    matches = anchors & {title_key(get_title(record) or "") for record in records}
    if len(matches) < 2:
        raise ValueError("Author profile did not match two known works")


def resolve_profile(profiles, papers, config, title_key, get_title):
    matches = []
    for author_id, name in profiles:
        if not author_id or title_key(name) != title_key(config["author"]):
            continue
        records = papers(author_id)
        try:
            verify_profile(records, config, title_key, get_title)
        except ValueError:
            continue
        matches.append(records)
    if len(matches) != 1:
        raise ValueError("Could not identify a unique author profile")
    return matches[0]
