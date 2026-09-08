# Contributing

One file per deal. A receipt for every term. **You do not need to know what a pull request is.**

## The easiest way: send the document

Email **deals@futurepickleballcourt.com** with a link to the agreement and the name of the place. That is the whole ask. I do the extraction.

There is a form on [the site](https://futurepickleballcourt.com) that writes the email for you, and a [GitHub issue form](https://github.com/apeabody007/futurepickleballcourt/issues/new?template=add-a-deal.yml) if you would rather use that.

A public link to the document is required. Submissions without one are closed unread. That is the only rule this list has.

## The most useful way: run the prompt first

If you run [the review prompt](https://futurepickleballcourt.com/prompt.txt) against the document, it ends by printing a filled-in submission block. Paste that into your email along with the link. That produces a far better entry than a raw document, because the section numbers are already pulled.

## The developer way: open a pull request

## Add a deal

1. Copy `deals/_template.json` to `deals/<id>.json`. The id is `country-state-locality-year`, lowercase, hyphens only. Example: `us-pa-lancaster-2025`. The filename must equal the `id` field.
2. Fill in what you can prove. Anything you cannot prove stays `null`. A `null` renders as "not enough information," which is honest. A guess renders as a fact, which is not.
3. For every clause where `present` is `true` or `false`, add at least one source. Prefer the executed document with a page or section:
   ```json
   "sources": [{ "url": "https://city.gov/agreement.pdf", "where": "Section 7.2, p. 14", "quote": "shall post a letter of credit" }]
   ```
   A bare URL string is allowed when that is all you have.
4. Set `verification`:
   - `primary`: you read the executed agreement, ordinance, or recorded document, and at least one entry in `documents` has `kind: "primary"`.
   - `press`: every term traces to a news report or government summary.
   - `unverified`: drafted from memory or a secondary summary. Say so. Someone else can upgrade it.
5. Run the build and commit its outputs:
   ```
   python3 scripts/build.py
   ```
   No dependencies, only python3. It validates your file against `schema/deal.schema.json`, scores it against `schema/floor.json`, and regenerates `BENCHMARK.md` and `docs/index.html`. CI runs the same script with `--check` and fails the pull request if the outputs are stale.
6. Open the pull request. In the description, say what you read and what you could not find.

## Correct a deal

Same process. Change the field, change or add the source, rebuild, explain in the pull request.

## Change the floor

`schema/floor.json` defines what each clause has to clear to count as meeting the model templates. Those rules are a position, not a law. If you think one is wrong, change it in a pull request and give the reason. Expect discussion.

## Add a clause

Adding a field to `schema/deal.schema.json` means every existing deal file needs that field. Open an issue first so I can agree it earns its place.

## What I do not accept

- Numbers without a source.
- Copies of the agreements themselves. Link to where they are hosted, or to the public records request that produced them.
- Deals described only by a company press release. Press releases can be a source for a term, not the only source for a deal.
