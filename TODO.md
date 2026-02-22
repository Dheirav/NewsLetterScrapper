# TODO

## Newsletter

- [ ] **Homogeneous section ordering** — group stories by domain so all World news appears together, all Business/Economy together, all Technology together, etc. Currently stories are ordered by user engagement score (adapter.py) which mixes domains. The renderer or assembler should sort/group by domain after personalisation ranking is applied.

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

- [ ] **Cache `sources.yaml` in `reliability.py`** — `_load_source_tiers()` reads and parses `sources.yaml` from disk on every cluster assessment. The result should be cached at module level (e.g. with `functools.lru_cache`) so the file is only read once per pipeline run.
