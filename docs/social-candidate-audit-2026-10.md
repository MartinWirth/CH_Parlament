# Social profile candidate audit — 2026-10-29

Scope: 18 review candidates in `social_review_candidates.json` (six people). This is a **source attribution audit**, not an authenticated account ownership check or live HTTP health test. Social networks can block automated fetching.

| Person | Candidates | Source evidence | Disposition |
| --- | ---: | --- | --- |
| Andri Silberschmidt | 4 | PDF on personal domain, dated 2021; individual handles need current verification | Manual review |
| Islam Alijaj | 4 | [Member-linked accounts](https://schwur.ch/it/parlamento/islam-alijaj) report links from personal site | Manual review; relatively strong attribution |
| Linda De Ventura | 2 | [Member page](https://schwur.ch/en/parliament/linda-de-ventura) cited in candidate data; direct account validation pending | Manual review |
| Simona Brizzi | 2 | [Member-linked accounts](https://schwur.ch/fr/parlement/simona-brizzi), plus [LinkedIn profile](https://ch.linkedin.com/in/simona-brizzi-6b115772) | Manual review; relatively strong attribution |
| Farah Rumy | 3 | [Wikidata](https://www.wikidata.org/wiki/Q107377457) lists all three handles; [LinkedIn profile](https://ch.linkedin.com/in/farah-rumy-46841b215) shows parliamentary activity | Manual review; LinkedIn relatively strong |
| Nina Schläfli | 3 | Historical 2019 election archive cited, [current party biography](https://www.sp-ps.ch/person/nina-schlaefli/) does not verify these account URLs | Hold; require current direct evidence |

## Identity and release policy

- No candidates are automatically approved. The runtime approval store is `.social_review_decisions.json`, separate from the candidate input file.
- Check that each handle is personally linked to the named MP, is currently accessible, and is not a party/team account.
- Approve via authenticated `POST /admin/review` only after checking the current snapshot; do not overwrite previously verified profiles.
- Run refresh before review so the current candidate queue is populated. Repeat review for each active language snapshot as needed.
- HTTP 403/429 from a social platform is **not** proof an account is missing; record as inconclusive.

## Checks completed

- Candidate JSON on main inspected: 18 entries, no duplicate person-number/URL pairs.
- Cross-checked Swiss parliament IDs for Islam Alijaj (10812), Simona Brizzi (10813), Farah Rumy (11506), Linda De Ventura (14105), Nina Schläfli (10826) and Andri Silberschmidt (4290) against the [Wikidata 52nd National Council roster](https://www.wikidata.org/wiki/Wikidata:WikiProject_every_politician/Switzerland/data/National_Council/52nd).
- Direct profile retrieval was not consistently available. **Zero accounts have been approved in this audit.**
