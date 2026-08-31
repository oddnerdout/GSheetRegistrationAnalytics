import os
import csv
import urllib.request
import io
import re
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# Securely retrieve Sheet ID from Environment Variables
# Set a default fallback for local development if needed


def fetch_sheet_data():
    """Downloads and parses the Google Sheet from the hidden SHEET_ID."""
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            csv_string_data = response.read().decode('utf-8')

        csv_reader = csv.reader(io.StringIO(csv_string_data))
        raw_rows = list(csv_reader)
        
        if len(raw_rows) < 4:
            return [], []
            
        headers = [h.strip() for h in raw_rows[3]]
        data_rows = raw_rows[4:] if len(raw_rows) > 4 else []
        cleaned_rows = [r for r in data_rows if any(cell.strip() for cell in r)]
        
        return headers, cleaned_rows
    except Exception as e:
        print(f"Error fetching sheet: {e}")
        return [], []

def find_col_idx(headers, keywords, fallback):
    if isinstance(keywords, str): keywords = [keywords]
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

    explicit_driver_yes = (d_lower.startswith('y') or d_lower == 'true' or 'driver' in d_lower)
    explicit_driver_no = (d_lower.startswith('n') or d_lower == 'false' or 'none' in d_lower)

    has_valid_space = bool(space_val) and not (
        s_lower in ['0', 'none', 'n/a', 'na', 'no', 'nil', '-'] or 
        'no space' in s_lower or 'no vehicle' in s_lower
    )

    is_driver = explicit_driver_yes or (has_valid_space and not explicit_driver_no)
    is_ride_req = (r_lower.startswith('y') or r_lower == 'true' or 'yes' in r_lower)

    seat_count = 0
    if is_driver and has_valid_space:
        nums = re.findall(r'\d+', space_val)
        if nums:
            seat_count = int(nums[0])
        elif 'van' in s_lower:
            seat_count = 6 

    return {
        'is_driver': is_driver,
        'explicit_driver_no': explicit_driver_no,
        'space_val': space_val,
        'seat_count': seat_count,
        'has_valid_space': has_valid_space,
        'is_ride_req': is_ride_req
    }

def infer_timing(status_val):
    status = status_val.lower()
    if "full time" in status or "full-time" in status or "all weekend" in status:
        return "Friday", "Sunday (Lord's Day)"
    elif "friday" in status and "overnight" in status:
        return "Friday", "Saturday"
    elif "saturday" in status and "overnight" in status:
        return "Saturday", "Sunday (Lord's Day)"
    elif "friday" in status:
        return "Friday", "Friday (Day Only)"
    elif "saturday" in status and ("day" in status or "offsite" in status or "day-only" in status):
        return "Saturday", "Saturday"
    elif "saturday" in status:
        return "Saturday", "Saturday (Day Only)"
    elif "sunday" in status or "lord's day" in status:
        return "Sunday (Lord's Day)", "Sunday (Lord's Day)"
    return "Unknown Timing", "Unknown Timing"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
    <title>Camp Comm Center</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #f2f2f7; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        .sticky-top-panel { position: sticky; top: 0; z-index: 1000; background-color: #ffffff; border-bottom: 1px solid #e5e5ea; padding: 12px; }
        .card-header-group { font-weight: bold; padding: 8px 12px; margin-top: 10px; border-radius: 6px; }
        .badge-req { color: #d73a49; font-weight: bold; }
        .badge-driver { color: #28a745; font-weight: bold; }
        .list-group-item { cursor: pointer; }
    </style>
</head>
<body>

<div class="sticky-top-panel shadow-sm">
    <div class="row g-2 mb-2">
        <div class="col-12">
            <select id="viewMode" class="form-select fw-bold" onchange="renderApp()">
                <option value="0">Classic View</option>
                <option value="1">Roster View (Dense)</option>
                <option value="2">To Camp 🚐</option>
                <option value="3">To NYC 🚐</option>
            </select>
        </div>
        <div class="col-6">
            <select id="hallFilter" class="form-select form-select-sm" onchange="renderApp()">
                <option value="ALL">📍 Halls: All</option>
            </select>
        </div>
        <div class="col-6">
            <select id="transFilter" class="form-select form-select-sm" onchange="renderApp()">
                <option value="ALL">🚗 Trans: All</option>
                <option value="DRIVERS">Drivers Only</option>
                <option value="RIDES">Ride Requests Only</option>
            </select>
        </div>
        <div class="col-12">
            <input type="text" id="searchInput" class="form-control form-control-sm" placeholder="Search attendees, rides, locations..." onkeyup="renderApp()">
        </div>
    </div>
</div>

<div class="container py-2">
    <div id="contentList"></div>
</div>

<!-- Details Modal -->
<div class="modal fade" id="detailModal" tabindex="-1">
  <div class="modal-dialog modal-dialog-centered">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="modalTitle">Attendee Details</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body" id="modalBody"></div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
let rawData = [];
let headers = [];
let colMap = {};

async function loadData() {
    let res = await fetch('/api/data');
    let json = await res.json();
    rawData = json.rows;
    headers = json.headers;
    colMap = json.col_map;
    
    // Populate Halls Dropdown
    let halls = new Set();
    rawData.forEach(r => {
        let h = r[colMap.hall] ? r[colMap.hall].trim() : "";
        if(h) halls.add(h);
    });
    let hallSelect = document.getElementById('hallFilter');
    Array.from(halls).sort().forEach(h => {
        let opt = document.createElement('option');
        opt.value = h;
        opt.innerText = "📍 Hall: " + h;
        hallSelect.appendChild(opt);
    });

    renderApp();
}

function renderApp() {
    let mode = parseInt(document.getElementById('viewMode').value);
    let hallFilter = document.getElementById('hallFilter').value;
    let transFilter = document.getElementById('transFilter').value;
    let query = document.getElementById('searchInput').value.toLowerCase();

    let filtered = rawData.filter(r => {
        let hallVal = r[colMap.hall] ? r[colMap.hall].trim() : "";
        if(hallFilter !== "ALL" && hallVal !== hallFilter) return false;

        let tState = r._tstate;
        if(transFilter === "DRIVERS" && !(tState.is_driver || tState.has_valid_space)) return false;
        if(transFilter === "RIDES" && !tState.is_ride_req) return false;

        if(query) {
            return r.some(cell => String(cell).toLowerCase().includes(query));
        }
        return true;
    });

    let container = document.getElementById('contentList');
    container.innerHTML = "";

    if(mode === 0 || mode === 1) { // Classic or Dense
        let currentGroup = "";
        let listGroup = document.createElement('div');
        listGroup.className = "list-group shadow-sm mb-3";

        filtered.forEach(r => {
            let gVal = r[colMap.hall] || "All Data";
            if(gVal !== currentGroup) {
                currentGroup = gVal;
                let header = document.createElement('div');
                header.className = "card-header-group bg-secondary text-white";
                header.innerText = "📍 " + gVal;
                container.appendChild(header);
                listGroup = document.createElement('div');
                listGroup.className = "list-group shadow-sm mb-3";
                container.appendChild(listGroup);
            }

            let item = document.createElement('a');
            item.className = "list-group-item list-group-item-action d-flex justify-content-between align-items-center";
            item.onclick = () => showDetail(r);

            let name = r[colMap.name] || "(Unnamed)";
            let status = r[colMap.status] || "";

            if(mode === 1) { // Dense
                item.innerHTML = `<span class="fw-bold fs-6">${name}</span> <small class="text-muted">${status}</small>`;
            } else { // Classic
                let sub = [];
                if(r._tstate.is_driver) sub.push("🚗 Driver (" + r._tstate.space_val + ")");
                if(r._tstate.is_ride_req) sub.push("🙋 Need Ride");
                
                item.innerHTML = `
                    <div>
                        <div class="fw-bold">${name}</div>
                        <small class="text-muted">${status} ${sub.length ? ' • ' + sub.join(' • ') : ''}</small>
                    </div>
                    <span class="text-muted">&rsaquo;</span>`;
            }
            listGroup.appendChild(item);
        });
    } else { // To Camp (2) / To NYC (3)
        let groupMath = {};
        filtered.forEach(r => {
            let grp = mode === 2 ? r._arrive : r._depart;
            if(!groupMath[grp]) groupMath[grp] = {needs: 0, seats: 0, rows: []};
            if(r._tstate.is_ride_req) groupMath[grp].needs++;
            if(r._tstate.is_driver) groupMath[grp].seats += r._tstate.seat_count;
            groupMath[grp].rows.push(r);
        });

        Object.keys(groupMath).forEach(grp => {
            let data = groupMath[grp];
            let deficit = data.needs - data.seats;
            let header = document.createElement('div');
            
            let badgeText = deficit > 0 ? `⚠️ Short ${deficit}` : `✅ Surplus ${-deficit}`;
            let bgClass = deficit > 0 ? "bg-danger text-white" : "bg-success text-white";
            
            header.className = `card-header-group ${bgClass} d-flex justify-content-between align-items-center`;
            header.innerHTML = `<span>🕒 ${grp}</span> <small>${badgeText} (Needs: ${data.needs} | Seats: ${data.seats})</small>`;
            container.appendChild(header);

            let listGroup = document.createElement('div');
            listGroup.className = "list-group shadow-sm mb-3";

            data.rows.forEach(r => {
                let item = document.createElement('a');
                item.className = "list-group-item list-group-item-action d-flex justify-content-between align-items-center";
                item.onclick = () => showDetail(r);

                let badge = "";
                if(r._tstate.is_driver) badge = `<span class="badge-driver">Driver (${r._tstate.space_val})</span>`;
                else if(r._tstate.is_ride_req) badge = `<span class="badge-req">Need Ride</span>`;

                item.innerHTML = `
                    <div>
                        <div class="fw-bold">${r[colMap.name] || "(Unnamed)"}</div>
                        <small class="text-muted">${r[colMap.status] || ""}</small>
                    </div>
                    <div>${badge}</div>`;
                listGroup.appendChild(item);
            });
            container.appendChild(listGroup);
        });
    }
}

function showDetail(row) {
    document.getElementById('modalTitle').innerText = row[colMap.name] || "Attendee Details";
    let body = document.getElementById('modalBody');
    body.innerHTML = "";
    headers.forEach((h, i) => {
        if(h && row[i]) {
            body.innerHTML += `<p class="mb-1"><strong>${h}:</strong> ${row[i]}</p>`;
        }
    });
    let modal = new bootstrap.Modal(document.getElementById('detailModal'));
    modal.show();
}

loadData();
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/data")
def get_data():
    headers, rows = fetch_sheet_data()
    
    col_name = find_col_idx(headers, ["full name", "name", "attendee", "participant"], 0)
    col_hall = find_col_idx(headers, ["locality", "hall", "locality/hall", "church"], -1)
    col_status = find_col_idx(headers, ["status", "registration type", "attending", "full time", "camp stay", "registration"], -1)
    col_ride = find_col_idx(headers, ["need a ride", "ride request", "need ride", "passenger"], -1)
    col_drive = find_col_idx(headers, ["give rides", "driver", "can you drive", "can you give"], -1)
    col_space = find_col_idx(headers, ["space", "capacity", "seats", "how much space", "vehicle"], -1)

    processed_rows = []
    for r in rows:
        tstate = parse_transport_state(r, col_drive, col_ride, col_space)
        status_val = r[col_status] if 0 <= col_status < len(r) else ""
        arrive, depart = infer_timing(status_val)
        
        # Attach dynamic backend processing metadata to row
        r_dict = list(r)
        r_dict.append(tstate) # _tstate
        r_dict.append(arrive) # _arrive
        r_dict.append(depart) # _depart
        processed_rows.append(r_dict)

    return jsonify({
        'headers': headers,
        'rows': [[*r[:-3]] for r in processed_rows],
        'col_map': {
            'name': col_name,
            'hall': col_hall,
            'status': col_status
        }
    })

# Attach pre-calculated properties on JSON conversion
@app.template_filter('tstate')
def get_tstate(r): return r[-3]

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
