import csv
import io
import os
import re
import time
import threading
import urllib.request
from flask import Flask, jsonify, render_template_string, request, make_response

app = Flask(name)

#Configuration

SHEET_ID = os.environ.get("SHEET_ID", "").strip()
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "60"))

#In-Memory Cache State & Concurrency Lock

cache_lock = threading.Lock()
cached_payload = None
cache_timestamp = 0
last_good_payload = None

def fetch_sheet_data_from_google():
"""Downloads and parses the Google Sheet from the hidden SHEET_ID."""
if not SHEET_ID:
print("[Error] SHEET_ID environment variable is not set.")
return [], []

url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"
try:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/115.0.0.0 Safari/537.36"
            )
        },
    )
    # 8 second timeout to avoid worker thread exhaustion
    with urllib.request.urlopen(req, timeout=8) as response:
        csv_string_data = response.read().decode("utf-8", errors="replace")

    # newline='' ensures multi-line cells aren't counted as multiple rows
    csv_reader = csv.reader(io.StringIO(csv_string_data, newline=''))
    raw_rows = list(csv_reader)

    if len(raw_rows) < 4:
        return [], []

    headers = [h.strip() for h in raw_rows[3]]
    data_rows = raw_rows[4:] if len(raw_rows) > 4 else []
    cleaned_rows = [r for r in data_rows if any(cell.strip() for cell in r)]

    return headers, cleaned_rows
except Exception as e:
    print(f"[Error] Google Sheet fetch failed: {e}")
    return [], []


def find_col_idx(headers, keywords, fallback):
if isinstance(keywords, str):
keywords = [keywords]
for kw in keywords:
for idx, h in enumerate(headers):
if kw.lower() in str(h).lower():
return idx
return fallback

def parse_transport_state(row, col_drive, col_ride, col_space):
def get_val(idx):
return str(row[idx]).strip() if 0 <= idx < len(row) else ""

driver_val = get_val(col_drive)
ride_val = get_val(col_ride)
space_val = get_val(col_space)

d_lower = driver_val.lower()
r_lower = ride_val.lower()
s_lower = space_val.lower()

explicit_driver_yes = (
    d_lower.startswith("y") or d_lower == "true" or "driver" in d_lower
)
explicit_driver_no = (
    d_lower.startswith("n") or d_lower == "false" or "none" in d_lower
)

has_valid_space = bool(space_val) and not (
    s_lower in ["0", "none", "n/a", "na", "no", "nil", "-"]
    or "no space" in s_lower
    or "no vehicle" in s_lower
)

is_driver = explicit_driver_yes or (
    has_valid_space and not explicit_driver_no
)
is_ride_req = (
    r_lower.startswith("y") or r_lower == "true" or "yes" in r_lower
)

seat_count = 0
if is_driver and has_valid_space:
    nums = re.findall(r"\d+", space_val)
    if nums:
        seat_count = int(nums[0])
    elif "van" in s_lower:
        seat_count = 6

return {
    "is_driver": is_driver,
    "explicit_driver_no": explicit_driver_no,
    "space_val": space_val,
    "seat_count": seat_count,
    "has_valid_space": has_valid_space,
    "is_ride_req": is_ride_req,
}


def infer_timing(status_val):
status = status_val.lower().strip()

if "sat" in status and ("night" in status or "part-time" in status or "part time" in status or "overnight" in status):
    return "Saturday", "Sunday (Lord's Day)"
elif "fri" in status and ("night" in status or "part-time" in status or "part time" in status or "overnight" in status):
    return "Friday", "Saturday"
elif "day-only" in status or "day only" in status or "offsite" in status or "1 day" in status or "(1 day)" in status:
    return "Saturday", "Saturday"
elif "full time" in status or "full-time" in status or "all weekend" in status or "full" in status:
    return "Friday", "Sunday (Lord's Day)"
elif "friday" in status or "fri" in status:
    return "Friday", "Friday (Day Only)"
elif "sunday" in status or "lord's day" in status or "lords day" in status:
    return "Sunday (Lord's Day)", "Sunday (Lord's Day)"
elif "saturday" in status or "sat" in status:
    return "Saturday", "Saturday"
    
return "Unknown Timing", "Unknown Timing"


def generate_fresh_payload():
"""Fetches and transforms the Google Sheet into the API response format."""
headers, rows = fetch_sheet_data_from_google()
if not headers and not rows:
return None

col_name = find_col_idx(headers, ["full name", "name", "attendee", "participant"], 0)
col_hall = find_col_idx(headers, ["locality", "hall", "locality/hall", "church"], -1)
col_district = find_col_idx(headers, ["district", "region", "area", "zone"], -1)
col_status = find_col_idx(
    headers,
    ["camp stay", "stay type", "status", "registration type", "attending", "full time", "registration"],
    -1,
)
col_ride = find_col_idx(headers, ["need a ride", "ride request", "need ride", "passenger"], -1)
col_drive = find_col_idx(headers, ["give rides", "driver", "can you drive", "can you give"], -1)
col_space = find_col_idx(headers, ["space", "capacity", "seats", "how much space", "vehicle"], -1)

processed_rows = []
for r in rows:
    tstate = parse_transport_state(r, col_drive, col_ride, col_space)
    status_val = r[col_status] if 0 <= col_status < len(r) else ""
    arrive, depart = infer_timing(status_val)

    processed_rows.append(
        {
            "data": r,
            "_tstate": tstate,
            "_arrive": arrive,
            "_depart": depart,
        }
    )

return {
    "headers": headers,
    "rows": processed_rows,
    "col_map": {
        "name": col_name,
        "hall": col_hall,
        "district": col_district,
        "status": col_status,
    },
    "cached_at": time.time(),
}


def get_cached_or_fresh_data(force_refresh=False):
"""
Thread-safe Cache accessor.
Prevents cache stampedes and serves stale data if Google Sheets fails.
"""
global cached_payload, cache_timestamp, last_good_payload
now = time.time()

# Fast Read Path (No lock needed for read if fresh)
if not force_refresh and cached_payload is not None and (now - cache_timestamp) < CACHE_TTL_SECONDS:
    return cached_payload, False

# Cache Expired or Forced Refresh: Synchronize with Lock
with cache_lock:
    now = time.time()
    if not force_refresh and cached_payload is not None and (now - cache_timestamp) < CACHE_TTL_SECONDS:
        return cached_payload, False

    fresh = generate_fresh_payload()
    if fresh is not None:
        cached_payload = fresh
        cache_timestamp = now
        last_good_payload = fresh
        return cached_payload, True
    elif last_good_payload is not None:
        print("[Warning] Serving stale cache due to upstream fetch failure.")
        return last_good_payload, False
    else:
        return {"headers": [], "rows": [], "col_map": {}}, False


HTML_TEMPLATE = """

        <input type="radio" class="btn-check" name="viewMode" id="vm1" value="1" onchange="renderApp()">
        <label class="btn btn-outline-primary btn-segment" for="vm1">Roster</label>

        <input type="radio" class="btn-check" name="viewMode" id="vm2" value="2" onchange="renderApp()">
        <label class="btn btn-outline-primary btn-segment" for="vm2">To Camp 🚐</label>

        <input type="radio" class="btn-check" name="viewMode" id="vm3" value="3" onchange="renderApp()">
        <label class="btn btn-outline-primary btn-segment" for="vm3">To NYC 🚐</label>

        <input type="radio" class="btn-check" name="viewMode" id="vm4" value="4" onchange="renderApp()">
        <label class="btn btn-outline-primary btn-segment" for="vm4">📊 Matrix</label>
    </div>

    <!-- Row 2: Filter Buttons (Halls & Transport) -->
    <div class="row g-2 mb-2">
        <div class="col-6">
            <button id="btnHalls" class="btn btn-primary btn-sm w-100 fw-bold text-truncate" onclick="openHallsModal()">📍 Halls: All</button>
        </div>
        <div class="col-6">
            <div class="dropdown">
                <button id="btnTransport" class="btn btn-warning text-dark btn-sm w-100 fw-bold dropdown-toggle text-truncate" type="button" data-bs-toggle="dropdown">
                    🚗 Trans: All
                </button>
                <ul class="dropdown-menu w-100 shadow">
                    <li><a class="dropdown-item" href="#" onclick="setTransportFilter('All')">All</a></li>
                    <li><a class="dropdown-item" href="#" onclick="setTransportFilter('Drivers Only')">Drivers Only</a></li>
                    <li><a class="dropdown-item" href="#" onclick="setTransportFilter('Ride Requests Only')">Ride Requests Only</a></li>
                </ul>
            </div>
        </div>
    </div>

    <!-- Row 3: Settings, Copy Link, and Refresh -->
    <div class="row g-2 mb-2">
        <div class="col-7">
            <button class="btn btn-secondary btn-sm w-100 fw-bold" style="background-color: #5856d6; border-color: #5856d6;" data-bs-toggle="modal" data-bs-target="#settingsModal">
                ⚙️ Settings
            </button>
        </div>
        <div class="col-3">
            <button class="btn btn-outline-primary btn-sm w-100 fw-bold" onclick="copyShareableLink()" title="Copy Link with Current Filters">
                🔗 Share
            </button>
        </div>
        <div class="col-2">
            <button id="btnRefresh" class="btn btn-success btn-sm w-100 fw-bold" onclick="refreshData(true)" title="Force Refresh Sheet Data">
                🔄
            </button>
        </div>
    </div>

    <!-- Row 4: Search -->
    <div>
        <input type="text" id="searchInput" class="form-control form-control-sm" placeholder="Search attendees, rides, locations..." oninput="renderApp()">
    </div>
</div>


@app.route("/")
def index():
resp = make_response(render_template_string(HTML_TEMPLATE))
resp.headers["Cache-Control"] = "public, max-age=300"
return resp

@app.route("/api/data")
def get_data():
force_refresh = request.args.get("refresh") in ["1", "true", "yes"]
data, was_fresh = get_cached_or_fresh_data(force_refresh=force_refresh)

resp = make_response(jsonify(data))
resp.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=60"
return resp


if name == "main":
port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port, debug=False)
