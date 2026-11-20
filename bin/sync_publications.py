#!/usr/bin/env python3
"""Propose additive bibliography changes from public catalogues or a BibTeX export."""

import argparse
import codecs
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from contextlib import redirect_stdout
from dataclasses import asdict, replace
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit

import latexcodec  # registers the Unicode LaTeX codec
import yaml
from pybtex.database import parse_string
from pybtex.database.input.bibtex import Parser

from publication_sources import (
    ScholarDetailsUnavailable,
    Work,
    google_scholar,
    openalex,
    semantic_scholar,
)

BIB_PATH = Path("_bibliography/papers.bib")
CONFIG_PATH = Path("bin/publications.yml")
BLOCKED = re.compile(
    r"\b(withdrawn|withdrawal|retracted|retraction|rejected|confidential|under review|"
    r"unpublished|submitted(?: to)?|not accepted|in preparation|desk reject(?:ed)?)\b",
    re.IGNORECASE,
)
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s\"<>\\{}|]+", re.IGNORECASE)
ARXIV_PATTERN = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v\d+)?", re.I)
WORKSHOP = re.compile(
    r"\bworkshops?\b|\b(?:ICMLW|DiffCoALG|ML4EMS|UncertaiNLP)\b", re.I
)
PUBLICATION_HOSTS = {
    "aclanthology.org",
    "dl.acm.org",
    "ieeexplore.ieee.org",
    "link.springer.com",
    "proceedings.mlr.press",
    "proceedings.neurips.cc",
    "www.mdpi.com",
    "www.sciencedirect.com",
}
ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
GROUPS = {"conference", "workshop", "journal"}


def decode(value):
    return codecs.decode(unescape(value), "ulatex")


def title_key(value):
    normalized = unicodedata.normalize("NFKD", decode(value).casefold())
    return "".join(char for char in normalized if char.isalnum())


def doi_id(value):
    candidate = re.sub(
        r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
        "",
        unquote(value.strip()),
        flags=re.I,
    )
    return candidate.casefold() if DOI_PATTERN.fullmatch(candidate) else ""


def arxiv_id(value):
    candidate = re.sub(
        r"^(?:https?://(?:www\.|export\.)?arxiv\.org/(?:abs|pdf)/|arxiv:\s*|10\.48550/arxiv\.)",
        "",
        value.strip(),
        flags=re.I,
    ).removesuffix(".pdf")
    return (
        re.sub(r"v\d+$", "", candidate.casefold())
        if ARXIV_PATTERN.fullmatch(candidate)
        else ""
    )


def work_ids(work):
    doi = doi_id(work.doi) or doi_id(work.url)
    arxiv = arxiv_id(work.arxiv) or arxiv_id(doi)
    if host(work.url) in ARXIV_HOSTS:
        arxiv = arxiv or arxiv_id(work.url)
    return doi, arxiv


def host(url):
    try:
        if not re.fullmatch(r"https://[^\s<>{}\"'\\]+", url) or "{%" in url:
            return ""
        parsed = urlsplit(url)
        return parsed.hostname if not parsed.username and not parsed.password else ""
    except ValueError:
        return ""


def author_key(name):
    name = decode(name).replace("*", "").replace("{", "").replace("}", "").strip()
    if "," in name:
        surname, _, given = name.partition(",")
    else:
        given, _, surname = name.rpartition(" ")
    return title_key(surname), tuple(filter(None, map(title_key, given.split())))


def matching_name(left, right):
    surname, given = left
    other_surname, other_given = right
    if not surname or surname != other_surname or not given or not other_given:
        return False
    return all(
        first == second or (first[0] == second[0] and min(len(first), len(second)) == 1)
        for first, second in zip(given, other_given)
    )


def author_matches(left, right):
    return matching_name(author_key(left), author_key(right))


def same_authors(left, right):
    first = list(map(author_key, left.authors))
    unmatched = list(map(author_key, right.authors))
    if not first or len(first) != len(unmatched):
        return False
    for author in first:
        matches = [
            index
            for index, other in enumerate(unmatched)
            if matching_name(author, other)
        ]
        if len(matches) != 1:
            return False
        unmatched.pop(matches[0])
    return True


def identity_match(left, right):
    doi, arxiv = work_ids(left)
    other_doi, other_arxiv = work_ids(right)
    return bool((doi and doi == other_doi) or (arxiv and arxiv == other_arxiv))


def title_match(left, right):
    return bool(
        title_key(left.title) and title_key(left.title) == title_key(right.title)
    )


def relation(left, right):
    """Return match, conflict, or no relationship."""
    if identity_match(left, right):
        return "match" if same_authors(left, right) else "conflict"
    if not title_match(left, right):
        return ""
    if not same_authors(left, right):
        return "conflict"
    # A same-title workshop contribution may be distinct from a main-track paper.
    # Only infer this when both records independently identify their venues.
    known_workshop = WORKSHOP.search(left.venue or left.status)
    other_workshop = WORKSHOP.search(right.venue or right.status)
    doi, _ = work_ids(left)
    other_doi, _ = work_ids(right)
    if (
        doi
        and other_doi
        and doi != other_doi
        and bool(known_workshop) != bool(other_workshop)
    ):
        main = right if known_workshop else left
        if main.group == "conference" and not (arxiv_id(doi) or arxiv_id(other_doi)):
            return ""
    if doi and other_doi and doi != other_doi:
        # arXiv DOI and publisher DOI can be two versions of the same work.
        if not arxiv_id(doi) and not arxiv_id(other_doi):
            return "conflict"
    return "match"


class ExportParser(Parser):
    """Keep records when the Scholar export reuses a citation key."""

    def process_entry(self, entry_type, key, fields):
        base = key
        counter = 2
        while key in self.data.entries:
            key = f"{base}-{counter}"
            counter += 1
        super().process_entry(entry_type, key, fields)


def read_bib(raw, source):
    bibliography = (
        ExportParser().parse_string(raw)
        if source == "Scholar BibTeX export"
        else parse_string(raw, "bibtex")
    )
    for key, entry in bibliography.entries.items():
        fields = entry.fields
        yield key, Work(
            fields.get("title", ""),
            tuple(str(person) for person in entry.persons.get("author", [])),
            fields.get("year", ""),
            source,
            kind=entry.type,
            venue=fields.get("booktitle")
            or fields.get("journal")
            or fields.get("school")
            or "",
            url=fields.get("url") or fields.get("html") or fields.get("pdf") or "",
            doi=fields.get("doi", ""),
            arxiv=fields.get("arxiv")
            or (
                fields.get("eprint", "")
                if fields.get("archiveprefix", "").casefold() == "arxiv"
                else ""
            ),
            status=" ".join(fields.get(field, "") for field in ("note", "status")),
            group=fields.get("publication_group", ""),
            volume=fields.get("volume", ""),
            pages=fields.get("pages", ""),
            number=fields.get("number", ""),
            publisher=fields.get("publisher", ""),
        )


def classification(work, config):
    """Return presentation group and precise public publication kind."""
    doi, arxiv = work_ids(work)
    venue = title_key(work.venue)
    conference_venue = re.sub(
        r"^proceedings\s+of\s+(?:the\s+)?(?:\d+(?:st|nd|rd|th)\s+)?",
        "",
        work.venue,
        flags=re.I,
    )
    is_main_track = venue in {
        title_key(value) for value in config["conference_venues"]
    } or (
        title_key(conference_venue)
        in {title_key(value) for value in config["conference_venues"]}
    )
    kind = work.kind.casefold()
    if (
        BLOCKED.search(f"{work.title} {work.venue} {work.status}")
        or kind == "unpublished"
    ):
        return "", ""
    if arxiv and (not venue or venue.startswith("arxiv") or (doi and arxiv_id(doi))):
        return "workshop", "preprint"
    if (
        kind == "preprint"
        and doi
        and host(work.url)
        in {
            "www.biorxiv.org",
            "www.medrxiv.org",
            "osf.io",
            "www.preprints.org",
        }
    ):
        return "workshop", "preprint"
    if "preprint" in kind:
        return "", ""
    journal_kind = kind in {"article", "journalarticle", "journal-article"}
    proceedings_kind = kind in {"conference", "inproceedings", "proceedings-article"}
    citation_coordinates = (
        journal_kind
        and work.volume
        and (work.pages or work.number)
        or proceedings_kind
        and work.pages
        and work.publisher
    )
    public_record = (
        (doi and not arxiv_id(doi))
        or host(work.url) in PUBLICATION_HOSTS
        or citation_coordinates
    )
    if not public_record or not venue:
        return "", ""
    if WORKSHOP.search(f"{work.venue} {work.status}"):
        return "workshop", "workshop"
    if proceedings_kind or is_main_track:
        return ("conference" if is_main_track else "workshop"), "conference"
    if journal_kind:
        medical = venue in {
            title_key(value) for value in config["journal_venues"]
        } or any(title_key(field) == "medicine" for field in work.fields)
        return ("journal" if medical else "workshop"), "journal"
    if kind in {
        "book",
        "booksection",
        "book-chapter",
        "incollection",
        "dissertation",
        "phdthesis",
        "mastersthesis",
    }:
        return "workshop", "other"
    return "", ""


def excluded(work, exclusions):
    doi, arxiv = work_ids(work)
    return any(
        (doi and doi == doi_id(value))
        or (arxiv and arxiv == arxiv_id(value))
        or title_key(work.title) == title_key(value)
        for value in exclusions
    )


def propose(existing, discovered, config):
    counts = Counter()
    owner = author_key(config["author"])
    coauthors = {
        author_key(name)
        for work in existing
        for name in work.authors
        if not matching_name(owner, author_key(name))
    }
    affiliations = {title_key(name) for name in config.get("author_affiliations", [])}
    accepted = []
    classified = []
    blocked = [
        work
        for work in discovered
        if work.kind == "unpublished"
        or BLOCKED.search(f"{work.title} {work.venue} {work.status}")
    ]
    for work in discovered:
        if excluded(work, config.get("exclude", [])) or any(
            work == item
            or identity_match(work, item)
            or (title_match(work, item) and same_authors(work, item))
            for item in blocked
        ):
            counts["excluded or nonpublic"] += 1
            continue
        if (
            not work.title
            or not re.fullmatch(r"(?:19|20)\d{2}", work.year)
            or not any(matching_name(owner, author_key(name)) for name in work.authors)
        ):
            counts["insufficient author or citation metadata"] += 1
            continue
        if work.source in {"OpenAlex", "Semantic Scholar"} and not (
            any(author in affiliations for author in map(title_key, work.affiliations))
            or any(
                matching_name(name, coauthor)
                for name in map(author_key, work.authors)
                for coauthor in coauthors
            )
            or any(relation(work, record) == "match" for record in existing)
            or any(
                other.source in {"Google Scholar", "Scholar BibTeX export"}
                and relation(work, other) == "match"
                for other in discovered
            )
        ):
            counts["catalogue attribution requires manual verification"] += 1
            continue
        group, kind = classification(work, config)
        if not group:
            counts["insufficient public venue or status evidence"] += 1
            continue
        classified.append((replace(work, group=group), kind))

    # Prefer publication records over preprints, then records with persistent identifiers.
    classified.sort(
        key=lambda item: (
            item[1] == "preprint",
            not bool(work_ids(item[0])[0]),
            item[0].source,
            title_key(item[0].title),
            item[0].year,
        )
    )
    for work, kind in classified:
        relations = [(other, relation(work, other)) for other in existing]
        matches = [other for other, match in relations if match == "match"]
        if len(matches) > 1 or any(match == "conflict" for _, match in relations):
            counts["ambiguous match requiring manual review"] += 1
            continue
        if matches:
            counts["already listed or duplicate"] += 1
            if (
                kind != "preprint"
                and classification(matches[0], config)[1] == "preprint"
            ):
                counts["possible published version requiring manual review"] += 1
            continue
        related = [(other, relation(work, other)) for other, _ in accepted]
        if any(match == "conflict" for _, match in related):
            counts["conflicting source records requiring manual review"] += 1
            continue
        duplicate = next((other for other, match in related if match == "match"), None)
        if duplicate:
            counts["already listed or duplicate"] += 1
            continue
        accepted.append((work, kind))
    # Avoid choosing a winner when two otherwise valid new records disagree.
    conflicts = {
        work
        for work, _ in classified
        for other, _ in classified
        if work != other and relation(work, other) == "conflict"
    }
    return [(work, kind) for work, kind in accepted if work not in conflicts], counts


def plain(value):
    # Imported data is text. Do not carry HTML, Liquid, or BibTeX source into the site.
    return " ".join(re.sub(r"[<>{}\\]", "", decode(value)).split())


def bib_entry(work, kind, used_keys, owner):
    stem = f"{author_key(owner)[0]}{work.year}{title_key(work.title)[:24]}"
    key = stem
    counter = 2
    while key.casefold() in used_keys:
        key = f"{stem}-{counter}"
        counter += 1
    used_keys.add(key.casefold())

    doi, arxiv = work_ids(work)
    url = work.url if host(work.url) else f"https://doi.org/{doi}" if doi else ""
    if kind == "preprint" and arxiv:
        url = f"https://arxiv.org/abs/{arxiv}"
    entry_type = {
        "conference": "inproceedings",
        "workshop": "inproceedings",
        "journal": "article",
    }.get(kind, "misc")
    authors = " and ".join(plain(name) for name in work.authors)
    fields = {
        "title": plain(work.title),
        "author": authors,
        "year": work.year,
        "publication_group": work.group,
        "abbr": kind.title() if kind != "other" else "Publication",
        "doi": doi,
        "eprint": arxiv,
        "archiveprefix": "arXiv" if arxiv else "",
        "url": url,
        "html": url,
        "volume": plain(work.volume),
        "pages": plain(work.pages),
        "number": plain(work.number),
        "publisher": plain(work.publisher),
    }
    venue = plain(work.venue)
    if kind in {"conference", "workshop"}:
        fields["booktitle"] = venue
    elif kind == "journal":
        fields["journal"] = venue
    if kind == "preprint":
        fields["note"] = "Preprint (arXiv)" if arxiv else "Preprint"
    elif kind == "other":
        fields["note"] = venue

    # Preserve capitalization on render. Braces are fixed here, outside imported values.
    fields["title"] = "{" + fields["title"] + "}"
    body = ",\n".join(
        f"  {name} = {{{value}}}" for name, value in fields.items() if value
    )
    return f"@{entry_type}{{{key},\n{body}\n}}"


class SourceUnavailable(Exception):
    """A diagnostic controlled by our adapters, with no remote values."""


def collect(sources):
    discovered, summary = [], []
    for label, source in sources:
        records = []
        try:
            records.extend(source())
        except Exception as error:
            reason = (
                str(error)
                if isinstance(error, SourceUnavailable)
                else type(error).__name__
            )
            status = (
                f"partial, {len(records)} candidate(s)" if records else "unavailable"
            )
            summary.append(f"- {label}: {status} ({reason})")
        else:
            summary.append(f"- {label}: {len(records)} candidate(s)")
        discovered.extend(records)
    return discovered, summary


def run_source(source, known_titles):
    """Bound total source time, retaining completed records after a timeout."""
    command = [sys.executable, __file__, "--source-worker", source]
    input_data = json.dumps(sorted(known_titles)).encode()
    error = ""
    try:
        result = subprocess.run(
            command, input=input_data, capture_output=True, timeout=110, check=True
        )
        output = result.stdout
    except subprocess.TimeoutExpired as timeout:
        # The worker flushes after each entry. A final partial line is discarded.
        complete, _, _ = (timeout.stdout or b"").rpartition(b"\n")
        output = complete
        error = "time limit reached"
    for line in output.splitlines():
        payload = json.loads(line)
        if "error" in payload:
            error = payload["error"]
        else:
            record = payload["record"]
            for field in ("authors", "affiliations", "fields"):
                record[field] = tuple(record[field])
            yield Work(**record)
    if error:
        raise SourceUnavailable(error)


def source_worker(source):
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    known_titles = set(json.load(sys.stdin))
    sources = {
        "openalex": lambda: openalex(config, title_key, author_matches),
        "semantic-scholar": lambda: semantic_scholar(config, title_key),
        "scholar": lambda: google_scholar(known_titles, title_key),
    }
    output = sys.stdout
    try:
        with redirect_stdout(sys.stderr):
            for work in sources[source]():
                print(json.dumps({"record": asdict(work)}), file=output, flush=True)
    except HTTPError as error:
        print(json.dumps({"error": f"HTTP {error.code}"}), file=output)
    except URLError:
        print(json.dumps({"error": "network unavailable"}), file=output)
    except ScholarDetailsUnavailable:
        print(
            json.dumps({"error": "some Scholar detail pages were unavailable"}),
            file=output,
        )
    except Exception as error:
        print(json.dumps({"error": type(error).__name__}), file=output)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) == 2 and argv[0] == "--source-worker":
        source_worker(argv[1])
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--import-bibtex", type=Path, metavar="EXPORT")
    parser.add_argument(
        "--discover", action="store_true", help="query the online sources"
    )
    parser.add_argument(
        "--skip-source",
        action="append",
        default=[],
        choices=["scholar", "openalex", "semantic-scholar"],
    )
    parser.add_argument(
        "--write", action="store_true", help="append proposals to papers.bib"
    )
    parser.add_argument(
        "--report", type=Path, help="write the review summary as Markdown"
    )
    args = parser.parse_args(argv)
    if not args.discover and not args.import_bibtex:
        parser.error("specify --discover, --import-bibtex, or both")

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    original = BIB_PATH.read_bytes()
    existing = dict(read_bib(original.decode("utf-8"), "curated BibTeX"))
    for key, work in existing.items():
        if work.group not in GROUPS:
            raise ValueError(f"Missing or invalid publication_group on {key}")

    discovered, summary = [], []
    if args.import_bibtex:
        imported = list(
            read_bib(
                args.import_bibtex.read_text(encoding="utf-8-sig"),
                "Scholar BibTeX export",
            )
        )
        discovered.extend(work for _, work in imported)
        summary.append(f"- Scholar BibTeX export: {len(imported)} candidate(s)")
    if args.discover:
        titles = {title_key(work.title) for work in [*existing.values(), *discovered]}
        online = {
            "openalex": "OpenAlex",
            "semantic-scholar": "Semantic Scholar",
            "scholar": "Google Scholar",
        }
        fetched, statuses = collect(
            [
                (label, lambda key=key: run_source(key, titles))
                for key, label in online.items()
                if key not in args.skip_source
            ]
        )
        discovered.extend(fetched)
        summary.extend(statuses)

    additions, counts = propose(list(existing.values()), discovered, config)
    used_keys = {key.casefold() for key in existing}
    blocks = [
        bib_entry(work, kind, used_keys, config["author"]) for work, kind in additions
    ]
    if blocks:
        separator = b"\n" if original.endswith(b"\n") else b"\n\n"
        proposal = original + separator + ("\n\n".join(blocks) + "\n").encode("utf-8")
        if len(list(read_bib(proposal.decode("utf-8"), "validation"))) != len(
            existing
        ) + len(blocks):
            raise ValueError("Bibliography validation failed")
        if args.write:
            if BIB_PATH.read_bytes() != original:
                raise ValueError(
                    "Bibliography changed during discovery. Run the sync again."
                )
            temporary = BIB_PATH.with_suffix(".bib.tmp")
            temporary.write_bytes(proposal)
            temporary.replace(BIB_PATH)

    report = [
        "## Sources",
        "",
        *summary,
        "",
        f"## Proposed additions ({len(additions)})",
        "",
    ]
    for work, kind in additions:
        title = re.sub(r"([\[\]()*_`@#|~])", r"\\\1", plain(work.title))
        report.append(
            f"- {title} ({work.year}) · {work.group.title()} · {kind} · {work.source}"
        )
    report += [
        "",
        "## Review counts",
        "",
        *[f"- {reason}: {count}" for reason, count in sorted(counts.items())],
    ]
    if args.report:
        args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    if not args.write:
        print("\nDry run. Use --write to append the proposed entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
