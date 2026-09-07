# What communities actually signed

**Live: [futurepickleballcourt.com](https://futurepickleballcourt.com)**

Data centers are the most profitable buildings going up right now, and a town gets one chance to negotiate with them: before the approvals are granted. Plenty of model agreements say what a town should ask for. What a county with a part-time attorney cannot get is the comparison: what did other places actually sign, term by term, and where does the offer on the table fall?

This is that comparison. Every signed deal, scored against the same ten terms, with a link to the page or section of the document that backs each one.

The name is Mark Cuban's line that a lot of data centers will end up as pickleball courts if efficiency gains keep coming. If he is right, the terms that matter are the ones written for that day: who tears it down, who is stuck with the grid bill, and whether the money promised to the town survives the tenant leaving.

## What you'll find

- **The scorecard.** One row per deal, one column per term. A stamp says whether the deal clears the bar for that term, falls short, or whether nobody has been able to find out yet. Tap a row for the receipts.
- **The receipts.** For each term, what the deal says, in plain language, followed by numbered citations to the executed agreement, ordinance, or the reporting that backs it.
- **The bar.** What each of the ten terms has to include to count as met. It is a position, not a law, and it lives in a text file anyone can propose changes to.

Deals are labeled by how well they are verified. *Read from the signed document* means a person read the executed agreement and cited its sections. *From news reports* means every term traces to reporting. *Not yet verified* means it was drafted from a summary and is waiting for someone to check it. Unknown terms are shown as unknown, never as a pass or a fail.

## The ten terms

Community fund · Clawbacks · Decommissioning · Grid costs · Water · Noise · Jobs · Local contracting · Transparency · Tax incentives

The exact bar for each is in [`schema/floor.json`](schema/floor.json) and on the site. The model documents it was drawn from are listed in [`templates/README.md`](templates/README.md), and the state laws that set a floor above the local deal are summarized in [`STATE_RULES.md`](STATE_RULES.md).

## Limits, read before you quote it

- **Small sample.** A handful of deals is a start, not a survey. Do not read a pattern here as the national picture until the count is much higher.
- **One reader per document.** "Read from the signed document" means a person read it and cited sections. It does not mean two people agreed. Corrections are welcome and expected.
- **Deals change.** Agreements get amended, voided, and superseded. Each record carries the date it was last reviewed. Check the primary document before you act on a term.
- **The bar is an opinion.** A deal that falls short here may still be a good deal for that town. The stamps say whether a term is present, not whether the whole deal was wise.
- **Unknown is not a failure.** A question mark means nobody has found the answer yet. Many terms live in tariffs, side letters, or records requests, not the headline agreement.

## Add a deal or fix one

If you know of a signed data center agreement that is not here, have a public records response, or have a page number for a term currently marked unknown, that is the most useful thing you can contribute.

1. Copy `deals/_template.json` to a new file named `country-state-locality-year.json`.
2. Fill in what you can prove. Leave the rest `null`. A `null` shows up as "we could not find out," which is honest. A guess shows up as a fact, which is not.
3. Cite every term, ideally with the section or page: `{ "url": "...", "where": "Section 7.2, p. 14" }`.
4. Run `python3 scripts/build.py` (no dependencies, only Python 3) and commit the regenerated `BENCHMARK.md` and `docs/index.html` along with your file.
5. Open a pull request and say what you read and what you could not find.

Full details in [CONTRIBUTING.md](CONTRIBUTING.md). Corrections work the same way. The build script validates every file against [`schema/deal.schema.json`](schema/deal.schema.json) and the same check runs on every pull request.

## How it's built

Plain files, no framework. `deals/` holds one JSON file per agreement. `scripts/build.py` validates them, scores them against the bar, and regenerates the markdown table in [`BENCHMARK.md`](BENCHMARK.md) and the site in `docs/`, which GitHub Pages serves. There is nothing to install.

## Who maintains this

Prepared by [Aaron Peabody](https://aaronpeabody.dev). I run technology for a paving and site-work contractor, and data centers need site work, so the terms about local contracting and road repair would benefit companies like mine. The terms I rank highest, decommissioning bonds and minimum bills, do nothing for my company. I think these facilities should get built. I also think the towns hosting them should not be the ones holding the bag if the boom turns. The way to keep me honest is the same as everyone else: a source on every term.

## License

Code is MIT. Deal records and the generated scorecard are CC BY 4.0. See [LICENSE](LICENSE).
