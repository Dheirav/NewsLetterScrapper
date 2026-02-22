# TODO

## Newsletter

- [x] **Homogeneous section ordering** — stories are now sorted by domain in `assembler.py` using `SECTION_ORDER` from `_domains.py` (World → AI → Technology → Economy → Science → Policy → Health). Engagement ranking from `adapter.py` is preserved within each section.

## Sources

- [ ] **Add source metadata for ranking and balancing**

  ### Goal
  Upgrade `sources.yaml` so the pipeline can distinguish between general news, analysis, and research sources, and control their influence during story ranking.

  ### Required Changes

  1. Update every source entry in `sources.yaml` to include:
     - `source_type` — one of `"news"`, `"analysis"`, `"research"`
     - `weight` — numeric ranking influence score

  2. Keep existing fields unchanged: `name`, `url`, `domain`, `tier`

  3. Weight rules (initial defaults):
     | source_type | tier 1 | tier 2 |
     |-------------|--------|--------|
     | news        | 1.0    | 0.9    |
     | analysis    | 0.7    | 0.6    |
     | research    | 0.4    | 0.3    |

  4. Update ingestion pipeline so each created `Article` includes:
     - `source_type`
     - `source_weight`

  ### Expected Outcome
  - Each article carries source metadata.
  - Future story ranking can use source weights.
  - Newsletter remains general-purpose while research sources act as supporting context.

## Email

- [ ] **Unsubscribe / opt-out mechanism** — there is currently no way for a recipient in `RECIPIENT_EMAILS` to remove themselves. Add an unsubscribe flow: include a one-click unsubscribe link in each email that marks the address as opted-out in the DB, and skip opted-out addresses in `send_email()`.

## Performance

- [x] **Cache `sources.yaml` in `reliability.py`** — already implemented via a module-level `_SOURCE_TIERS_CACHE` global in `reliability.py`. The file is only read once per process.
