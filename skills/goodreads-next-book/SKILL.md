---
name: goodreads-next-book
description: >
  Choose the user's next book by searching their Goodreads Want-to-Read shelf first,
  applying request-time criteria, and preferring a shelf book unless none reasonably
  match. Use when the user asks "what should I read next", "pick my next book", wants
  a book off their Goodreads to-read shelf, or filters like "sci-fi under 300 pages I've
  been ignoring" / "highest-rated book I've been meaning to read".
---

# goodreads-next-book

Recommends the user's next read from their Goodreads **Want to Read** shelf. A
deterministic script handles retrieval, structured filtering, and scoring; the agent
handles the semantic reasoning and any enrichment.

## Two-layer contract

1. **Deterministic script (`next-book`)** — fetches the full shelf over RSS, applies
   structured hard filters (genre, author, rating, page count, publication year, date
   added, unrated), scores survivors with **shelf age as a first-class signal**, cross-
   references the **read** shelf so a book that comes later in a series than you've
   reached is demoted (with a redirect to the right next entry), and returns a best /
   alternative / wildcard shortlist.
2. **You (the agent)** — translate the user's natural request into script flags, then
   resolve genuinely semantic criteria (tone, "light vacation read", themes, audiobook
   quality) on the **returned shortlist only**, enriching via Open Library (by ISBN,
   ~75% of books) then web search. Do not enrich the whole shelf. **Series order is
   handled deterministically** (see below); your remaining job on series is only the
   edge cases the title marker can't express — companions, sub-series naming, or a book
   the user read outside Goodreads.

## Invocation

```text
next-book [SOURCE] [filters…]
```

- `SOURCE` is the user's Goodreads **numeric user id** (e.g. `12345678`) or a full RSS
  URL. If omitted, the script resolves the source in this order: `GOODREADS_USER_ID`
  (a bare id — the common case, not sensitive), then `GOODREADS_TO_READ_RSS_URL` (a full
  URL). If none is set it exits with a clear error — ask the user for their id.
- A full URL may contain a private `key=…` (needed only for a **private** shelf).
  **Treat the URL and any key as a secret:** never echo it back, commit it, or place it
  in output. The script never prints it. A bare user id is not sensitive.

The environment variables let a user configure their id once (e.g. in a shell profile or
a `~/.env` sourced by `~/.zprofile`) rather than passing it each run. The skill only reads
the variables from the environment; loading a `.env` file is the shell's job, not the
script's.

Run via PATH if installed, else fall back to `python3 <skill-dir>/next-book`, where
`<skill-dir>` is the directory containing this SKILL.md.

### Filters (map the user's request onto these)

| Request phrasing | Flag |
|---|---|
| genre / shelf | `--genre science-fiction` (repeatable; aliases like `sci-fi`, `ya` expand) |
| by an author | `--author "Le Guin"` |
| well-rated | `--min-rating 4.2` |
| under / over N pages | `--max-pages 300` / `--min-pages 400` |
| published era | `--published-before 2000` / `--published-after 2015` |
| added long ago / recently | `--added-years-ago 3`, `--added-before/--after YYYY-MM-DD` |
| digital / physical / audio | `--format digital` (also `physical`, `audio`, `owned`, `library`; repeatable) |
| "been ignoring" (unrated) | `--unrated` |
| long-neglected vs fresh interest | `--prefer neglected` / `--prefer recent` |
| how many to show | `--limit 3` (default) |
| where series progress is read from | `--series-shelf read` (default) |
| ignore series order entirely | `--no-series-check` |
| machine-readable output | `--json` (for programmatic enrichment) |

Example — "highest-rated sci-fi I've been ignoring for years":
`next-book 12345678 --genre sci-fi --unrated --min-rating 4.2 --prefer neglected`

## Retrieval

The script paginates the shelf RSS endpoint (`page=1..N`, `per_page=100`) and
deduplicates by `book_id`, recovering the entire shelf (the feed caps at 100 items per
response and ignores `order`, so pagination is the only reliable full-shelf strategy).

Unless `--no-series-check` is set, it then paginates the **read** shelf the same way to
learn how far you've progressed in each series. If that shelf can't be read (network or
parse error), the run continues with a warning and no series-order awareness rather than
failing.

## Output

Well-formed Markdown (or JSON with `--json`):

- **Shortlist** — numbered candidates, each with average rating, page count, date
  added, shelves, series/position, and Goodreads link. Roles: best match, strong
  alternative, wildcard. A shortlisted book that is out of series order is flagged
  inline and points at the **Series order** section.
- A coverage line (`Fetched N; matched M; showing K`).
- **Series order** — only when the read-shelf check demoted a series entry. Lists each
  affected series with how far you've read and the earliest still-unread entry on your
  to-read shelf to pick it up from. Present this so the user can course-correct; don't
  lead with a demoted book.
- **Needs enrichment** — only when a page filter is set and a matching book has no page
  count in the feed. Look these up before excluding them.
- **Needs enrichment (format/availability)** — only when `--format` is set and an
  otherwise-matching book has no format shelf tag. The feed carries no edition format, so
  resolve these via Open Library by ISBN (its `physical_format` and ebook/Internet-Archive
  links), then web search. Report the result **as enrichment**, and note that an edition
  existing is not proof the user can access it (they may not own it or have it from a
  library).

Present ~3 recommendations to the user: lead with the best match and why it fits, then
the alternative and the wildcard. Enrich finalists as needed to satisfy semantic
constraints, and say when a claim (e.g. tone or length) came from enrichment rather than
Goodreads.

**Speak in plain language — never surface raw source codes.** Translate any enrichment
API values into everyday terms before they reach the user. In particular, map Open
Library's `ebook_access` (an Internet-Archive-only signal) as:

| Raw value | Say instead |
|---|---|
| `public` | "free to read online" (public domain) |
| `borrowable` | "free to borrow online (library-style, one copy at a time)" |
| `printdisabled` | "not freely readable online — the only online scan is restricted to readers with a print disability; a paid ebook may still exist" |
| `no_ebook` | "no free online edition found (a paid ebook may still exist)" |

Likewise translate `physical_format` ("paperback"/"hardcover") plainly, and always add
that Open Library only sees free/Internet-Archive copies, not paid Kindle/store
availability — so a "no free edition" result is not proof the book is unavailable
digitally.

## Known limitations

- **Page count** is present for ~98% of books but a few lack it; those surface under
  *Needs enrichment* when a page filter is active rather than being silently dropped.
- **`user_date_added`** is Goodreads' date-added, which may predate the actual move onto
  the to-read shelf. Use it for recent-interest / long-neglected scoring, but qualify
  precise shelf-tenure claims.
- Semantic criteria (tone, themes, "light read") are **not** in the feed — resolve them
  yourself on the shortlist.
- **Series order** is read from the title's `(Series, #N)` marker and matched against the
  read shelf by exact series name. It therefore misses cases the marker can't express: a
  series with no `#N` (omnibus, some companions), a book read outside Goodreads, or the
  same world split across differently-named sub-series (e.g. Percy Jackson's several
  arcs). Treat the deterministic result as strong but not exhaustive; if a shortlisted
  book smells like a mid-series or companion volume, confirm before recommending it.
- **Format / availability** (digital vs physical) is **not** an edition field in the feed.
  `--format` filters on the user's own shelf tags (`kindle`, `ebook`, `owned`, `audiobook`,
  …) via `reference/format-vocabulary.json`, so it only decides books the user has tagged;
  untagged matches surface under *Needs enrichment (format/availability)* for an Open
  Library lookup. This tells you an edition *exists* in a given format, not that the user
  can access it right now — treat precise ownership/library-availability claims as unknown.

## Dependencies

`python3` (standard library only). Enrichment steps use the agent's web/Open Library access.
