"""Search OpenAlex (https://openalex.org) for papers and datasets from the command line.

    python -m ora.literature "tunnel radio propagation measurements"
    python -m ora.literature "underground mine wireless" --datasets --limit 15 --json

Uses the public REST API. No key is needed for light use; set OPENALEX_API_KEY to use your own
free key and its larger daily allowance. Results are bibliographic metadata, not vetted claims.
"""
import argparse
import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.openalex.org/works"
FIELDS = "id,title,publication_year,cited_by_count,doi,type,open_access,primary_location"


def search(query, limit=10, datasets=False, since=None, sort="relevance_score:desc"):
    """Works matching ``query``: list of dicts with title, year, citations, type, doi, url and source."""
    filters = [f"title_and_abstract.search:{query}"]
    if datasets:
        filters.append("type:dataset")
    if since:
        filters.append(f"from_publication_date:{since}-01-01")
    params = {"filter": ",".join(filters), "per-page": min(limit, 50), "select": FIELDS, "sort": sort}
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    with urlopen(Request(API + "?" + urlencode(params), headers={"User-Agent": "ora-literature"}), timeout=60) as response:
        payload = json.load(response)
    rows = []
    for work in payload.get("results", []):
        location = work.get("primary_location") or {}
        rows.append(dict(title=work.get("title"), year=work.get("publication_year"), cited_by=work.get("cited_by_count"),
                         type=work.get("type"), doi=work.get("doi"),
                         url=(work.get("open_access") or {}).get("oa_url") or location.get("landing_page_url") or work.get("id"),
                         source=(location.get("source") or {}).get("display_name"), openalex=work.get("id")))
    return dict(query=query, total=payload.get("meta", {}).get("count"), results=rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Search OpenAlex for papers and datasets.")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--datasets", action="store_true", help="only works typed as datasets")
    parser.add_argument("--since", type=int, help="earliest publication year")
    parser.add_argument("--cited", action="store_true", help="sort by citations instead of relevance")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    out = search(args.query, args.limit, args.datasets, args.since, "cited_by_count:desc" if args.cited else "relevance_score:desc")
    if args.json:
        json.dump(out, sys.stdout, indent=1)
        return
    print(f"{out['total']} matches for: {out['query']}")
    for row in out["results"]:
        print(f"- [{row['year']}] {row['title']} ({row['type']}, cited {row['cited_by']})\n    {row['source'] or ''}  {row['doi'] or row['url']}")


if __name__ == "__main__":
    main()
