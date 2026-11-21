"""Browse the exported Division D meeting calendar."""

from base64 import b64encode
import calendar
import csv
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pydeck as pdk
import streamlit as st

from meeting_events import ADDRESS_COLUMN, FORMAT_COLUMNS, LOCATION_COLUMN, MEETINGS_CSV

from geocode_meetings import COORDINATES


def display_time(value):
    return time.fromisoformat(value).strftime("%I:%M %p").lstrip("0") if value else "All day"


def display_format(row):
    virtual, in_person = (
        (row.get(column) or "").strip().casefold() == "yes" for column in FORMAT_COLUMNS
    )
    return {
        (True, True): "Hybrid",
        (True, False): "Virtual Only",
        (False, True): "In-Person Only",
        (False, False): "Not specified",
    }[virtual, in_person]


st.set_page_config(page_title="Division D Club Compass", layout="wide")
logo = Path(__file__).with_name("compass_logo.svg").read_bytes()
logo_src = "data:image/svg+xml;base64," + b64encode(logo).decode("ascii")
with st.container(key="compass-banner"):
    st.html("""
    <style>
    [data-testid="stHeader"],
    [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu,
    [data-testid="stStatusWidget"] {
        display: none !important;
    }
    [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
        background-color: #edf4f6;
    }
    [data-testid="stMainBlockContainer"] {
        padding-top: 1rem;
    }
    .st-key-compass-banner {
        position: sticky;
        top: 0;
        z-index: 1000;
        background-color: #edf4f6;
        padding: .4rem 0;
    }
    .st-key-compass-filters {
        background-color: #fff;
        border-radius: 14px;
    }
    .compass-hero {
        display: flex; align-items: center; justify-content: space-between;
        gap: 1rem; padding: .8rem clamp(1rem, 3vw, 2rem); border-radius: 16px;
        background: linear-gradient(120deg, #082b3a, #00506a); color: #fff;
        margin-bottom: .65rem;
    }
    .compass-hero h1 {
        color: #fff; padding: 0; margin: .2rem 0 .35rem;
        font-size: clamp(1.65rem, 3vw, 2.6rem); line-height: 1.08;
    }
    .compass-hero p { color: #d8edf2; margin: 0; font-size: 1.05rem; }
    .compass-kicker { color: #f5d782; letter-spacing: .15em; font-size: .75rem; font-weight: 700; }
    .compass-hero .compass-logo {
        width: clamp(74px, 10vw, 116px); height: auto; flex-shrink: 0;
        filter: drop-shadow(0 10px 16px #031d2b80);
    }
    @media (max-width: 560px) {
        .compass-hero { gap: .65rem; }
        .compass-hero .compass-logo { width: clamp(54px, 13vw, 74px); }
        .compass-hero h1 { font-size: clamp(1.35rem, 6vw, 2rem); overflow-wrap: anywhere; }
    }
    </style>
    <header class="compass-hero">
        <div>
            <div class="compass-kicker">DISTRICT 220 · DIVISION D</div>
            <h1>Division D Club Compass</h1>
            <p>Find a Toastmasters club that fits your schedule</p>
        </div>
        <img class="compass-logo" src="COMPASS_LOGO_SRC" alt="" width="180" height="180">
    </header>
    """.replace("COMPASS_LOGO_SRC", logo_src))
if not MEETINGS_CSV.exists():
    st.info("Generate the meetings CSV first: python3 meeting_events.py --month 2026-11 --export")
    st.stop()

exported_at = datetime.fromtimestamp(MEETINGS_CSV.stat().st_mtime).astimezone()
st.caption(
    "Browse meeting dates and local start times from the latest CSV export. "
    f"Last updated: {exported_at:%b %d, %Y at %I:%M %p %Z}."
)

with MEETINGS_CSV.open(encoding="utf-8", newline="") as source:
    rows = list(csv.DictReader(source))
for row in rows:
    row["day"] = date.fromisoformat(row["date"])

november = date(2026, 11, 1)
months = sorted({november, date(2026, 12, 1), *(date(2027, m, 1) for m in range(1, 7)), *(row["day"].replace(day=1) for row in rows)})
weekdays = list(calendar.day_name)
calendar_weekday_order = (calendar.SUNDAY, *range(calendar.MONDAY, calendar.SUNDAY))
first_time, last_time = time(0, 0), time(23, 59)


def reset_filters():
    st.session_state["filter_revision"] = revision + 1


revision = st.session_state.get("filter_revision", 0)

with st.container(border=True, key="compass-filters"):
    month_col, time_col, reset_col = st.columns([3, 3, 1.4],
                                               vertical_alignment="top")
    month_label, month_input = month_col.columns([1, 3], vertical_alignment="center")
    month_label.markdown("Month")
    month = month_input.selectbox("Month", months, index=months.index(november),
                                format_func=lambda day: day.strftime("%B %Y"),
                                key=f"filter_month_{revision}", label_visibility="collapsed")
    time_label, time_input = time_col.columns([2, 3], vertical_alignment="center")
    time_label.markdown("Meeting start time")
    start_time, stop_time = time_input.slider(
        "Meeting start time", first_time, last_time, (first_time, last_time),
        step=timedelta(minutes=1), format="h:mm A", key=f"filter_time_{revision}",
        label_visibility="collapsed",
    )
    with reset_col.container(horizontal=True, horizontal_alignment="right"):
        st.button("Reset filters", on_click=reset_filters)

    days_label, days_input = month_col.columns([1, 3], vertical_alignment="center")
    days_label.markdown("Days")
    with days_input.container(horizontal=True, gap="small"):
        selected = {i for i in calendar_weekday_order
                    if st.checkbox(weekdays[i][:3], key=f"filter_day_{i}_{revision}")}
    month_col.caption("No days checked = all days.")

    format_label, format_input = time_col.columns([2, 3], vertical_alignment="center")
    format_label.markdown("Meeting format")
    with format_input.container(horizontal=True, gap="small"):
        formats = {column for column, label in zip(FORMAT_COLUMNS, ("Virtual", "In-Person"))
                   if st.checkbox(label, key=f"format_{column}_{revision}")}

by_day = defaultdict(list)
for row in rows:
    day = row["day"]
    if day.replace(day=1) != month or (selected and day.weekday() not in selected):
        continue
    if formats and not any((row.get(column) or "").strip().casefold() == "yes"
                           for column in formats):
        continue
    if row["time"]:
        clock = time.fromisoformat(row["time"]).replace(second=0, microsecond=0, tzinfo=None)
        if not start_time <= clock <= stop_time:
            continue
    elif (start_time, stop_time) != (first_time, last_time):
        continue
    by_day[day].append(row)

today = date.today()
upcoming = {day: meetings for day, meetings in by_day.items() if day >= today}
meeting_count = sum(len(meetings) for meetings in upcoming.values())
venues = defaultdict(list)
for day, meetings in sorted(upcoming.items()):
    for row in meetings:
        if (row.get("Option_InPerson") or "").strip().casefold() != "yes":
            continue
        address = (row.get(ADDRESS_COLUMN) or "").strip()
        if address:
            venues[address].append((day, row))
coords = json.loads(COORDINATES.read_text()) if COORDINATES.exists() else {}
points = [
    {**coords[address], "details": "\n".join(
        f"{day:%a, %b %d, %Y} · {display_time(row['time'])}\n{row['club_name']}"
        for day, row in meetings
    )}
    for address, meetings in venues.items() if address in coords
]

location_count = len(points)
location_label = "location" if location_count == 1 else "locations"
calendar_tab, map_tab, list_tab = st.tabs([
    "Calendar",
    f"Map ({location_count} in-person {location_label})",
    f"Upcoming Meetings ({meeting_count})",
])
with calendar_tab:
    with st.container(horizontal=True, horizontal_alignment="right"):
        list_all = st.toggle("Expand all", key=f"calendar_view_{revision}")
    st.html("""
        <style>
        [class*="st-key-calendar-today-"] {
            background-color: #fff7d6;
            color: #222;
        }
        [class*="st-key-calendar-past-"] {
            background-color: #f1f2f4;
            color: #222;
        }
        [class*="st-key-calendar-"][class*="-single-meeting"] summary {
            background: linear-gradient(90deg, #e3f6e8, #f5fbf6);
            color: #161616;
        }
        [class*="st-key-calendar-"][class*="-multiple-meetings"] summary {
            background: linear-gradient(90deg, #94d5a5, #d9f2e0);
            color: #161616;
        }
        </style>
    """)
    for col, i in zip(st.columns(7), calendar_weekday_order):
        col.text(weekdays[i][:3])
    for week in calendar.Calendar(firstweekday=calendar.SUNDAY).monthdatescalendar(
        month.year, month.month
    ):
        for col, day in zip(st.columns(7), week):
            meetings = by_day.get(day, [])
            state = ("outside" if day.month != month.month else
                     "today" if day == today else "past" if day < today else "future")
            highlight = ""
            if state in {"today", "future"} and meetings:
                highlight = "-single-meeting" if len(meetings) == 1 else "-multiple-meetings"
            with col.container(border=state == "outside",
                               key=f"calendar-{state}-{day.isoformat()}{highlight}"):
                if day.month == month.month:
                    count = len({row["club_name"] or row["url"] for row in meetings})
                    label = f"**{day.day}**" + (" · Today" if day == today else "")
                    club_label = f"{count} {'club' if count == 1 else 'clubs'}"
                    label += f" · {club_label}"
                    with st.expander(label, expanded=list_all,
                                     key=f"day-{day.isoformat()}-{revision}-{list_all}"):
                        for row in meetings:
                            st.text(f"{display_time(row['time'])}\n{row['club_name']}")
                        if not meetings:
                            st.caption("No matching meetings.")
                else:
                    st.text(" ")
with map_tab:
    mapped_meetings = [row for address, meetings in venues.items() if address in coords
                       for _, row in meetings]
    club_count = len({row["club_name"] or row["url"] for row in mapped_meetings})
    counts = ((len(points), "location"), (club_count, "club"),
              (len(mapped_meetings), "upcoming meeting"))
    st.markdown(" · ".join(f"**{count}** {label}{'' if count == 1 else 's'}"
                           for count, label in counts))
    st.caption(
        "Pins show venues for upcoming meetings you can attend in person, "
        "including hybrid meetings. Hover over a pin for meeting dates, start times, and club names."
    )
    if formats == {"Option_Virtual"}:
        st.caption("With Virtual selected, only hybrid meetings appear on the map. "
                   "Virtual-only meetings are listed under Upcoming Meetings.")
    if points:
        view = pdk.data_utils.compute_view(
            [(point["longitude"], point["latitude"]) for point in points]
        )
        view.zoom = min(view.zoom, 14)
        layer = pdk.Layer(
            "ScatterplotLayer", points, get_position="[longitude, latitude]",
            get_fill_color=[220, 38, 38, 220], radius_min_pixels=7, pickable=True,
        )
        st.pydeck_chart(pdk.Deck(
            layers=[layer], initial_view_state=view,
            tooltip={"text": "{details}", "style": {"whiteSpace": "pre-line"}},
        ))
    if venues:
        st.dataframe([
            {"Address": address,
             "Building": ", ".join(sorted({
                 row[LOCATION_COLUMN] for _, row in meetings if row.get(LOCATION_COLUMN)
             })),
             "Clubs": ", ".join(sorted({row["club_name"] for _, row in meetings})),
             "On map": address in coords}
            for address, meetings in sorted(venues.items())
        ], hide_index=True)
        if len(points) < len(venues):
            st.caption("Some addresses are listed without a pin. Run geocode_meetings.py to refresh map coordinates.")
    else:
        st.info("No upcoming meetings with street addresses match the current filters.")

with list_tab:
    if upcoming:
        st.dataframe([
            {"Date": day.strftime("%a, %b %d"), "Start time": display_time(row["time"]),
             "Club": row["club_name"], "Meeting format": display_format(row)}
            for day in sorted(upcoming) for row in upcoming[day]
        ], hide_index=True)
    else:
        st.info("No upcoming meetings match these filters.")
