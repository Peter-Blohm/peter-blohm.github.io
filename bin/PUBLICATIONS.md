# Publication sync

Jekyll Scholar uses `_bibliography/papers.bib` for both the publications page and the homepage. Each entry has a `publication_group` of `conference`, `workshop`, or `journal`. The homepage uses the existing `selected` field. Set `selected = {true}` explicitly when you want to feature a paper.

## Import a Google Scholar export

Select all works in your Google Scholar profile and export them as BibTeX. Keep the downloaded file outside the repository. Run these commands from the repository root in a Python virtual environment.

```sh
python -m pip install -r bin/publication-requirements.txt
python bin/sync_publications.py --import-bibtex ~/Downloads/scholar.bib
python bin/sync_publications.py --import-bibtex ~/Downloads/scholar.bib --discover
```

Both commands preview proposals. The second adds online discovery and can corroborate records with sparse export metadata. Use `--write` to append the displayed proposals, then review the BibTeX diff before opening a PR. Use `--report /path/to/report.md` to save the report.

The import keeps entries with duplicate export keys long enough to compare their metadata. A DOI, an arXiv record, a recognized publisher link, or complete publication coordinates can support a proposal. Ambiguous authorship, uncertain status, and conflicting metadata are held for review. The report counts withheld records without printing their titles. When a published version appears for an existing preprint, review and edit the existing entry yourself to retain its citation key and links.

## Weekly review

**Discover publications** runs Mondays at 06:15 UTC and can also be started from the Actions tab. It opens a PR only when the proposed bibliography changes and the rendered site passes validation. You can edit the proposed entries in the PR. New runs leave an open review PR intact. Add a declined work's DOI, arXiv ID, or exact title to `exclude` in `bin/publications.yml` to suppress future proposals.

The workflow uses the built-in GitHub token. Enable **Allow GitHub Actions to create and approve pull requests** in the repository's Actions settings if PR creation is disabled. Review and merge the PR manually. The workflow never approves or merges it. GitHub may ask you to approve the PR's workflow runs.

The author IDs in `bin/publications.yml` were checked against known papers. Because public indexes can merge namesakes, each OpenAlex or Semantic Scholar record also needs corroboration from a known affiliation, known coauthor, an existing entry, or the Scholar profile or export. The optional repository secrets `OPENALEX_API_KEY` and `SEMANTIC_SCHOLAR_API_KEY` improve reliability where rate limits apply. Google Scholar access can be incomplete, so its BibTeX export remains the offline import route.

## Grouping

- **Conference** uses the main-track venue list in `bin/publications.yml`.
- **Workshop** includes workshops, other ML venues, public preprints, and academic work such as theses. The entry shows its actual venue or preprint status.
- **Journal** contains medical journal articles. The configured venue list and catalogue subjects inform this placement.

For a specific paper, edit `publication_group` directly. To customize the badge, set `abbr` to a short venue or publication-type label. Sync runs never replace an existing entry, and the publication list keeps the existing year order within each section.
