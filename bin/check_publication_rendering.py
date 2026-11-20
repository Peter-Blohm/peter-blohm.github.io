"""Verify that Jekyll renders each entry in its assigned section."""

import re
import sys
from pathlib import Path

from pybtex.database import parse_string

from sync_publications import BIB_PATH


def main():
    site = Path(sys.argv[1])
    publications = (site / "publications/index.html").read_text(encoding="utf-8")
    homepage = (site / "index.html").read_text(encoding="utf-8")
    sections = re.split(r'<h2 id="(conference|workshop|journal)"[^>]*>', publications)
    assert sections[1::2] == [
        "conference",
        "workshop",
        "journal",
    ], "Unexpected publication sections"
    contents = dict(zip(sections[1::2], sections[2::2]))
    bibliography = parse_string(BIB_PATH.read_text(encoding="utf-8"), "bibtex")
    for key, entry in bibliography.entries.items():
        marker = f'id="{key}"'
        group = entry.fields["publication_group"]
        assert (
            publications.count(marker) == 1 and marker in contents[group]
        ), f"Incorrect listing: {key}"
        selected = entry.fields.get("selected", "").casefold() == "true"
        assert homepage.count(marker) == int(
            selected
        ), f"Incorrect homepage visibility: {key}"
    print(f"Verified {len(bibliography.entries)} publications and homepage selection")


if __name__ == "__main__":
    main()
