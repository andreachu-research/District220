"""List events for the current month on a Toastmasters FullCalendar page."""

import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import date, datetime, time
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

MEETINGS_CSV = Path(__file__).resolve().parent / "data" / "available_meetings.csv"

FORMAT_COLUMNS = ("Option_Virtual", "Option_InPerson")
LOCATION_COLUMN = "InPerson_Location"
ADDRESS_COLUMN = "Address"
IN_PERSON_COLUMNS = (LOCATION_COLUMN, ADDRESS_COLUMN)
MEETING_METADATA = (*FORMAT_COLUMNS, *IN_PERSON_COLUMNS)


def fetch(url):
    request = Request(url, headers={"User-Agent": "CompassCalendar/0.1"})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8-sig"), response.url


def fetch_with_curl(url):
    """Use curl's certificate trust settings when Python's differ."""
    response = subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location",
         "--max-time", "10", "--proto", "=https,http", "--proto-redir", "=https,http",
         "--write-out", "\n%{url_effective}", "--", url],
        capture_output=True, text=True, encoding="utf-8",
    )
    if response.returncode:
        raise OSError(response.stderr.strip() or "curl failed")
    body, _, effective_url = response.stdout.rpartition("\n")
    return body.lstrip("\ufeff"), effective_url


def list_events(url, month=None, fetch_url=fetch, *, through=None):
    """Return events from month through the optional inclusive last month."""
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("Supply an http or https meetings URL.")

    first = (month or date.today()).replace(day=1)
    last = (through or first).replace(day=1)
    if last < first:
        raise ValueError("The final month must be on or after the first month.")
    following = date(last.year + (last.month == 12), last.month % 12 + 1, 1)
    query = urlencode({
        key: datetime.combine(day, time.min).astimezone().isoformat()
        for key, day in (("start", first), ("end", following))
    })

    page, page_url = fetch_url(url)
    config = re.search(r"\beventSources\s*:\s*\[(.*?)\]", page, re.S)
    feeds = re.findall(r"""\burl\s*:\s*['"]([^'"]+)['"]""", config[1]) if config else []
    if not feeds:
        raise ValueError("No supported FullCalendar event feeds found on this page.")

    events = []
    for feed in dict.fromkeys(feeds):
        feed_url = urljoin(page_url, feed)
        separator = "&" if urlsplit(feed_url).query else "?"
        try:
            body, _ = fetch_url(feed_url + separator + query)
            entries = json.loads(body)
            if not isinstance(entries, list):
                raise ValueError("Expected a JSON list of events.")
            for entry in entries:
                raw_start = entry["start"]
                if len(raw_start) == 10:
                    start = date.fromisoformat(raw_start)
                    local_start = datetime.combine(start, time.min)
                else:
                    local_start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
                    if local_start.tzinfo is not None:
                        local_start = local_start.astimezone()
                    start = local_start.date()
                if first <= start < following:
                    events.append((local_start.replace(tzinfo=None), {
                        "title": entry["title"],
                        "start": start.isoformat() if entry.get("allDay") or len(raw_start) == 10
                        else local_start.isoformat(),
                        "url": urljoin(page_url, entry["url"]) if entry.get("url") else None,
                    }))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Could not read calendar feed {feed_url}: {exc}") from exc
    return [event for _, event in sorted(events, key=lambda item: item[0])]


def read_meeting_sources(csv_path, url_column, enabled_column="Meetings_Bin", *, limit=None):
    """Read club names and distinct calendar URLs from rows enabled with 'yes'."""
    if limit is not None and limit < 1:
        raise ValueError("Limit must be at least 1.")
    with open(csv_path, encoding="utf-8-sig", newline="") as source:
        rows = csv.DictReader(source)
        headers = {name.strip().casefold(): name for name in rows.fieldnames or []}
        for name in (url_column, enabled_column, "Club Name"):
            if name.casefold() not in headers:
                raise ValueError(f"Missing CSV column: {name}")
        column = headers[url_column.casefold()]
        enabled = headers[enabled_column.casefold()]
        club = headers["club name"]
        urls = {}
        for row in rows:
            if (row.get(enabled) or "").strip().casefold() != "yes":
                continue
            url = (row.get(column) or "").strip()
            parts = urlsplit(url)
            if parts.scheme in {"http", "https"} and parts.netloc:
                metadata = {name: (row.get(headers.get(name.casefold())) or "").strip()
                            for name in FORMAT_COLUMNS}
                in_person = metadata["Option_InPerson"].casefold() == "yes"
                metadata.update({
                    name: (row.get(headers.get(name.casefold())) or "").strip() if in_person else ""
                    for name in IN_PERSON_COLUMNS
                })
                urls.setdefault(url, {
                    "url": url,
                    "club_name": (row.get(club) or "").strip(),
                    **metadata,
                })
                if limit is not None and len(urls) >= limit:
                    break
        return list(urls.values())


def read_meeting_urls(csv_path, url_column, enabled_column="Meetings_Bin", *, limit=None):
    return [source["url"] for source in
            read_meeting_sources(csv_path, url_column, enabled_column, limit=limit)]


def lookup_meeting_times(url, month=None, fetch_url=fetch, *, through=None):
    """Return the date and local start time for each meeting at this URL."""
    meetings = []
    for event in list_events(url, month=month, fetch_url=fetch_url, through=through):
        day, _, clock = event["start"].partition("T")
        meetings.append({"date": day, "time": clock or None})
    return meetings


def run_workflow(csv_path, url_column, month=None, fetch_url=fetch, *, limit=None, through=None):
    """Look up each calendar independently so one failure won't stop the batch."""
    month = month or date.today()
    results = []
    for source in read_meeting_sources(csv_path, url_column, limit=limit):
        try:
            meetings = lookup_meeting_times(source["url"], month, fetch_url, through=through)
            results.append({**source, "meetings": meetings})
        except (OSError, ValueError) as exc:
            results.append({**source, "error": str(exc)})
    return results


def write_meetings_csv(results, output=MEETINGS_CSV):
    """Write one row per meeting and report failed calendars separately."""
    rows = [
        {"club_name": club["club_name"], "url": club["url"],
         **{name: club.get(name, "") for name in MEETING_METADATA}, **meeting}
        for club in results for meeting in club.get("meetings", [])
    ]
    rows.sort(key=lambda row: (row["date"], row["time"] or "", row["club_name"]))
    with open(output, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["club_name", "date", "time", "url", *MEETING_METADATA])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main():
    default_csv = Path(__file__).resolve().parent / "data/division_d_population.csv"
    url_column = "meetings"
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("url", nargs="?", help="Look up one Toastmasters meetings page")
    source.add_argument("--csv", type=Path, default=default_csv,
                        help=f"Calendar CSV (default: {default_csv.name})")
    parser.add_argument("--limit", type=int, help="Maximum number of eligible CSV URLs to look up")
    parser.add_argument("--month", type=lambda value: date.fromisoformat(value + "-01"),
                        help="Calendar month (YYYY-MM; defaults to the current month)")
    parser.add_argument("--through", type=lambda value: date.fromisoformat(value + "-01"),
                        help="Include months through YYYY-MM")
    parser.add_argument("--export", action="store_true", help=f"Write {MEETINGS_CSV.name}")
    parser.add_argument("--use-curl", action="store_true", help="Fetch calendars using system curl")
    args = parser.parse_args()
    if args.limit is not None and args.url:
        parser.error("--limit applies only to CSV input")
    if args.export and args.url:
        parser.error("--export applies only to CSV input")
    if args.through and args.through < (args.month or date.today()).replace(day=1):
        parser.error("--through must be on or after the first month")
    try:
        fetch_url = fetch_with_curl if args.use_curl else fetch
        results = (list_events(args.url, args.month, fetch_url, through=args.through) if args.url
                   else run_workflow(args.csv, url_column, args.month, fetch_url, limit=args.limit, through=args.through))
        if args.export:
            for club in results:
                if "error" in club:
                    print(f"{club['club_name']}: {club['error']}", file=sys.stderr)
            if results and all("error" in club for club in results):
                parser.exit(1, "No calendars could be read. Existing export left unchanged.\n")
            print(f"Wrote {write_meetings_csv(results)} meetings to {MEETINGS_CSV}")
        else:
            print(json.dumps(results, indent=2, ensure_ascii=False))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
