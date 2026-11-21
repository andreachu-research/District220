"""Cache Census geocoder coordinates for addresses in the meetings export."""

import csv
import json
from urllib.parse import urlencode

from meeting_events import ADDRESS_COLUMN, MEETINGS_CSV, fetch_with_curl

COORDINATES = MEETINGS_CSV.with_name("meeting_coordinates.json")


def main():
    with MEETINGS_CSV.open(newline="", encoding="utf-8") as source:
        addresses = {row[ADDRESS_COLUMN] for row in csv.DictReader(source) if row[ADDRESS_COLUMN]}
    points = json.loads(COORDINATES.read_text()) if COORDINATES.exists() else {}
    for address in sorted(addresses - points.keys()):
        query = address if "washington" in address.casefold() else f"{address}, Washington, DC"
        url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?" + urlencode({
            "address": query, "benchmark": "Public_AR_Current", "format": "json",
        })
        try:
            body, _ = fetch_with_curl(url)
            matches = json.loads(body)["result"]["addressMatches"]
            if matches:
                coords = matches[0]["coordinates"]
                points[address] = {"latitude": coords["y"], "longitude": coords["x"]}
            else:
                print(f"No match: {address}")
        except (OSError, ValueError, KeyError) as exc:
            print(f"Could not geocode {address}: {exc}")
    COORDINATES.write_text(json.dumps(points, indent=2, sort_keys=True) + "\n")
    print(f"Mapped {len(addresses & points.keys())} of {len(addresses)} addresses.")


if __name__ == "__main__":
    main()
