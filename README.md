# Data center deals

**What communities actually signed, clause by clause, with a receipt for every term.**

Live benchmark: https://apeabody007.github.io/datacenter-deals/

## The problem this solves

Data centers are the most profitable buildings anyone is putting up right now, and the leverage to negotiate with them peaks once, before approvals are granted. Model agreements already exist that say what a community should ask for. What a county negotiator with a part-time attorney cannot get is the comparison: forty signed deals side by side, so they can see they are being handed a bottom-quartile offer.

Nobody needs another template. This repository is the benchmark.

## How it works

- `deals/` holds one JSON file per signed, approved, or proposed agreement. Every clause carries its own sources, ideally the page or section of the executed document.
- `schema/deal.schema.json` defines the clauses we track: community fund, clawbacks, decommissioning, grid costs, water, noise, jobs, local contracting, transparency, and tax incentives.
- `schema/floor.json` defines what each clause has to clear to count as meeting what the model templates ask for. It is data, not a rule handed down, and it changes by pull request.
- `scripts/build.py` validates every deal, scores it against the floor, and regenerates [`BENCHMARK.md`](BENCHMARK.md) and the site in `docs/`. Standard library only.
- `templates/` links to the model documents we benchmark against. We do not copy them.

Every deal carries a verification level. `primary` means a person read the actual agreement. `press` means every term traces to reporting. `unverified` means it was drafted from a secondary summary and is waiting for someone to check it. Unknown terms render as unknown, never as a pass or a fail.

## Add a deal

Copy `deals/_template.json`, fill in what you can prove, run `python3 scripts/build.py`, open a pull request. Details in [CONTRIBUTING.md](CONTRIBUTING.md). Corrections are pull requests too.

If you have a signed agreement that is not here, a public records response, or a page number for a term currently marked unknown, that is the most valuable thing you can contribute.

## Who maintains this

Aaron Peabody ([aaronpeabody.dev](https://aaronpeabody.dev)). I run technology for a paving and site-work contractor, and data centers need site work, so the terms about local contracting and road repair would benefit companies like mine. The terms I rank highest, decommissioning bonds and minimum bills, do nothing for my company. I believe these facilities should get built. I also believe the towns hosting them should not be the ones holding the bag if the boom turns. Both can be true, and the way to keep me honest is the same as everyone else: a source on every term.

## License

Code is MIT. Deal records and the generated benchmark are CC BY 4.0. See [LICENSE](LICENSE).
