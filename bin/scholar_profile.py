"""Shared access to the public Google Scholar profile."""

from pathlib import Path


def load_scholar_user_id() -> str:
    import yaml

    with Path("_data/socials.yml").open(encoding="utf-8") as config:
        author_id = yaml.safe_load(config).get("scholar_userid")
    if not author_id:
        raise ValueError("Missing scholar_userid in _data/socials.yml")
    return author_id


def fetch_scholar_publications(author_id: str) -> list:
    from scholarly import scholarly

    scholarly.set_timeout(15)
    scholarly.set_retries(2)
    author = scholarly.search_author_id(author_id)
    publications = scholarly.fill(author, sections=["publications"]).get("publications")
    if not isinstance(publications, list):
        raise ValueError("Scholar profile did not include a publication list")
    return publications
