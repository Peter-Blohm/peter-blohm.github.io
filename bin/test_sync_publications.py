import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import yaml

import sync_publications as sync
from publication_sources import Work, resolve_profile

CONFIG = yaml.safe_load(Path("bin/publications.yml").read_text(encoding="utf-8"))
PAPER = Work(
    "An Example of Model Composition",
    ("Blohm, Peter", "Garg, Vikas K."),
    "2026",
    "test fixture",
    kind="inproceedings",
    venue="ICLR",
    doi="https://doi.org/10.1000/example",
)


class PublicationSyncTests(unittest.TestCase):
    def test_title_identifiers_and_authorship(self):
        self.assertEqual(
            sync.title_key("The {M}üller Model"),
            sync.title_key(r"The M{\"u}ller-Model"),
        )
        self.assertEqual(sync.work_ids(PAPER)[0], "10.1000/example")
        self.assertEqual(
            sync.arxiv_id("https://arxiv.org/pdf/2601.00123v2.pdf"), "2601.00123"
        )
        variant = replace(
            PAPER,
            title="renamed",
            doi="DOI: 10.1000/EXAMPLE",
            authors=("P. Blohm", "Vikas Garg"),
        )
        self.assertEqual(sync.relation(PAPER, variant), "match")
        self.assertEqual(
            sync.relation(
                PAPER, replace(variant, authors=("Phil Blohm", "Vikas Garg"))
            ),
            "conflict",
        )

    def test_verified_workshop_and_main_track_can_coexist(self):
        workshop = replace(PAPER, venue="ICLR Workshop", doi="10.1000/workshop")
        additions, _ = sync.propose([], [PAPER, workshop], CONFIG)
        self.assertEqual(
            {(work.group, kind) for work, kind in additions},
            {
                ("conference", "conference"),
                ("workshop", "workshop"),
            },
        )

    def test_suppresses_rejected_withdrawn_and_unknown_records(self):
        protected = [
            replace(
                PAPER, title=f"Fixture {status}", doi=f"10.1000/{index}", status=status
            )
            for index, status in enumerate(
                ("Rejected", "withdrawn", "confidential", "under review", "retracted")
            )
        ]
        protected += [
            replace(PAPER, kind="unpublished"),
            replace(PAPER, title="Unknown venue", doi="", venue=""),
            replace(PAPER, title="Different researcher", authors=("Phil Blohm",)),
            # A contrary public status on another source prevents a proposal.
            replace(PAPER, title="Alternate source", source="OpenAlex"),
        ]
        additions, _ = sync.propose([], protected, CONFIG)
        self.assertEqual(additions, [])

    def test_prefers_publication_to_preprint_and_labels_standalone_preprint(self):
        preprint = replace(
            PAPER,
            kind="preprint",
            venue="",
            doi="",
            url="https://arxiv.org/abs/2601.00123v3",
        )
        additions, _ = sync.propose([], [preprint, PAPER], CONFIG)
        self.assertEqual(
            [(work.group, kind) for work, kind in additions],
            [("conference", "conference")],
        )
        additions, _ = sync.propose([], [preprint], CONFIG)
        work, kind = additions[0]
        self.assertEqual((work.group, kind), ("workshop", "preprint"))
        bib = sync.bib_entry(work, kind, set(), CONFIG["author"])
        self.assertIn("note = {Preprint (arXiv)}", bib)
        self.assertIn("{https://arxiv.org/abs/2601.00123}", bib)

    def test_conflicting_dois_are_held_and_exclusions_apply(self):
        other = replace(PAPER, doi="10.1000/other")
        additions, _ = sync.propose([], [other, PAPER], CONFIG)
        self.assertEqual(additions, [])
        additions, _ = sync.propose(
            [], [PAPER], {**CONFIG, "exclude": ["10.1000/example"]}
        )
        self.assertEqual(additions, [])

    def test_medical_journal_and_non_top_conference(self):
        journal = replace(PAPER, kind="article", venue="Journal of Clinical Medicine")
        non_top = replace(PAPER, venue="A different conference")
        self.assertEqual(sync.classification(journal, CONFIG), ("journal", "journal"))
        self.assertEqual(
            sync.classification(non_top, CONFIG), ("workshop", "conference")
        )

    def test_one_unavailable_source_does_not_block_another(self):
        def unavailable():
            raise TimeoutError("private information")

        works, summary = sync.collect(
            [("Blocked source", unavailable), ("Available source", lambda: [PAPER])]
        )
        self.assertEqual(works, [PAPER])
        self.assertIn("TimeoutError", summary[0])
        self.assertNotIn("private information", summary[0])

    def test_profile_resolution_rejects_namesake(self):
        good = [{"title": title} for title in CONFIG["identity_papers"][:2]]
        records = {"namesake": [{"title": "Unrelated books"}], "correct": good}
        self.assertEqual(
            resolve_profile(
                [(key, "Peter Blohm") for key in records],
                records.__getitem__,
                CONFIG,
                sync.title_key,
                lambda record: record["title"],
            ),
            good,
        )

    def test_mixed_catalogue_profile_requires_work_level_evidence(self):
        namesake = replace(
            PAPER,
            title="Namesake Book",
            source="OpenAlex",
            kind="book",
            doi="10.1000/book",
        )
        partner = replace(PAPER, title="Prior work", doi="10.1000/prior")
        affiliated = replace(
            PAPER,
            source="OpenAlex",
            authors=("Peter Blohm",),
            affiliations=("TU Wien",),
        )
        namesake = replace(namesake, authors=("Peter Blohm",))
        self.assertEqual(
            sync.propose([partner], [namesake, affiliated], CONFIG)[0],
            [
                (
                    replace(affiliated, group="conference"),
                    "conference",
                )
            ],
        )

    def test_export_duplicate_keys_and_missing_doi(self):
        text = (
            "@article{reused, title={First Work}, author={Peter Blohm}, journal={Bioengineering}, "
            "year={2025}, volume={10}, pages={432}}\n"
            "@article{reused, title={Second Work}, author={Peter Blohm}, journal={Bioengineering}, "
            "year={2025}, volume={10}, pages={654}}\n"
        )
        records = list(sync.read_bib(text, "Scholar BibTeX export"))
        self.assertEqual(len(records), 2)
        self.assertEqual(
            [
                kind
                for _, kind in sync.propose([], [work for _, work in records], CONFIG)[
                    0
                ]
            ],
            [
                "journal",
                "journal",
            ],
        )

    def test_export_is_additive_repeatable_and_dry_by_default(self):
        before = Path("_bibliography/papers.bib").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            bib = Path(tmp) / "papers.bib"
            bib.write_bytes(before)
            export = Path(tmp) / "export.bib"
            export.write_text(
                "@inproceedings{fixture, title={An Example of Model Composition}, "
                "author={Blohm, Peter and Garg, Vikas K.}, year={2026}, booktitle={ICLR}, "
                "doi={10.1000/example}}\n",
                encoding="utf-8",
            )
            report = Path(tmp) / "report.md"
            with patch.object(sync, "BIB_PATH", bib), patch("builtins.print"):
                args = ["--import-bibtex", str(export), "--report", str(report)]
                sync.main(args)
                self.assertEqual(bib.read_bytes(), before)
                self.assertIn("Proposed additions (1)", report.read_text())
                sync.main([*args, "--write"])
                written = bib.read_bytes()
                self.assertTrue(written.startswith(before))
                self.assertGreater(len(written), len(before))
                self.assertNotIn("selected", written[len(before) :].decode())
                sync.main([*args, "--write"])
                self.assertEqual(bib.read_bytes(), written)
                self.assertIn("Proposed additions (0)", report.read_text())


if __name__ == "__main__":
    unittest.main()
