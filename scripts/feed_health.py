"""
scripts/feed_health.py
-----------------------
Report which configured sources are actually producing usable articles.

The gap this closes: the pipeline degrades silently when a feed dies. Bad feeds
are skipped by design ("failures degrade, they do not raise"), so a source that
stops returning entries simply disappears from the briefing with no error
anywhere. Reuters and Associated Press — the two highest-weighted tier-1 wires,
and the ones reliability.py name-checks — had both been dead for an unknown
period before anyone noticed.

Two failure modes, deliberately reported separately:

  SILENT     the feed returns no entries at all. Broken URL, retired feed,
             or the host blocking us.
  UNUSABLE   entries arrive but the articles never scrape above
             MIN_CONTENT_LENGTH, so only the RSS summary survives. Paywalls
             look like this. The source consumes fetch and embedding budget and
             contributes almost nothing.

Usage:
    python scripts/feed_health.py              # live probe of every feed
    python scripts/feed_health.py --from-db    # judge from stored articles instead
    python scripts/feed_health.py --days 7     # DB window (default 7)
    python scripts/feed_health.py --quiet      # only report problems

Exit codes: 0 all healthy, 1 problems found.
"""
import argparse
import asyncio
import sys
from dataclasses import dataclass

import feedparser
import httpx
import trafilatura
from sqlalchemy import text

from core.config import settings
from services.ingestion.source_catalog import load_catalog

UA = {"User-Agent": "Mozilla/5.0 (compatible; IntelligenceBot/1.0)"}
SAMPLE = 3          # articles scraped per feed when probing live


@dataclass
class Health:
    name: str
    tier: int
    weight: float
    entries: int = 0
    scraped_ok: int = 0
    sampled: int = 0
    error: str = ""

    @property
    def status(self) -> str:
        if self.error or self.entries == 0:
            return "SILENT"
        if self.sampled and self.scraped_ok == 0:
            return "UNUSABLE"
        return "OK"


async def probe_live(name: str, meta: dict) -> Health:
    h = Health(name, meta.get("tier", 3), meta.get("weight", 1.0))
    try:
        r = httpx.get(meta["url"], timeout=20, headers=UA, follow_redirects=True)
        parsed = feedparser.parse(r.text)
        h.entries = len(parsed.entries)
        if h.entries == 0:
            h.error = f"HTTP {r.status_code}, no entries"
            return h
    except Exception as exc:
        h.error = f"{type(exc).__name__}"
        return h

    async with httpx.AsyncClient(headers=UA, follow_redirects=True, timeout=20) as client:
        for entry in parsed.entries[:SAMPLE]:
            link = getattr(entry, "link", None)
            if not link:
                continue
            h.sampled += 1
            try:
                page = await client.get(link)
                body = trafilatura.extract(page.text, include_comments=False,
                                           include_tables=False)
                if body and len(body) >= settings.min_content_length:
                    h.scraped_ok += 1
            except Exception:
                pass
    return h


async def judge_from_db(days: int) -> list[Health]:
    """
    Cheaper and more honest than a live probe: it reports what the pipeline
    actually got, over a real window, rather than one sample right now.
    """
    from core.db.session import get_session

    catalog = load_catalog()
    async with get_session() as s:
        rows = (await s.execute(text("""
            SELECT source,
                   count(*) AS n,
                   sum(CASE WHEN content_quality = 'low' THEN 1 ELSE 0 END) AS low
            FROM articles
            WHERE created_at >= NOW() - make_interval(days => :d)
            GROUP BY source"""), {"d": days})).fetchall()
    seen = {r[0]: (r[1], r[2]) for r in rows}

    out = []
    for name, meta in catalog.items():
        h = Health(name, meta["tier"], meta["weight"])
        n, low = seen.get(name, (0, 0))
        h.entries = n
        h.sampled = n
        h.scraped_ok = n - low
        if n == 0:
            h.error = f"no articles in {days} days"
        out.append(h)
    return out


def report(results: list[Health], quiet: bool) -> int:
    silent = [h for h in results if h.status == "SILENT"]
    unusable = [h for h in results if h.status == "UNUSABLE"]
    ok = [h for h in results if h.status == "OK"]

    print(f"{len(results)} sources: {len(ok)} ok, {len(silent)} silent, "
          f"{len(unusable)} unusable\n")

    if silent:
        print("SILENT — configured but producing nothing:")
        for h in sorted(silent, key=lambda x: (x.tier, -x.weight)):
            print(f"  tier {h.tier} w{h.weight:<4} {h.name:30s} {h.error}")
        print()
    if unusable:
        print("UNUSABLE — entries arrive but nothing scrapes above "
              f"{settings.min_content_length} chars:")
        for h in sorted(unusable, key=lambda x: (x.tier, -x.weight)):
            print(f"  tier {h.tier} w{h.weight:<4} {h.name:30s} "
                  f"{h.entries} entries, 0/{h.sampled} usable")
        print()
    if ok and not quiet:
        print("OK:")
        for h in sorted(ok, key=lambda x: -x.scraped_ok):
            rate = f"{h.scraped_ok}/{h.sampled}" if h.sampled else "-"
            print(f"  tier {h.tier} w{h.weight:<4} {h.name:30s} "
                  f"{h.entries:4d} entries, {rate} usable")

    return 1 if (silent or unusable) else 0


async def main(from_db: bool, days: int, quiet: bool) -> int:
    if from_db:
        results = await judge_from_db(days)
    else:
        # Sequential on purpose: this scrapes real articles from every source,
        # and hammering 38 hosts at once is exactly the behaviour that gets a
        # scraper blocked.
        results = [await probe_live(n, m) for n, m in load_catalog().items()]
    return report(results, quiet)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Report which feeds are actually working")
    ap.add_argument("--from-db", action="store_true",
                    help="judge from stored articles instead of probing live")
    ap.add_argument("--days", type=int, default=7, help="window for --from-db")
    ap.add_argument("--quiet", action="store_true", help="only report problems")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.from_db, a.days, a.quiet)))
