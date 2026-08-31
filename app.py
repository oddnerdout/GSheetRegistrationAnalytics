import csv
import io
import os
import re
import urllib.request
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

# Retrieve Sheet ID from Environment Variables
SHEET_ID = os.environ.get("SHEET_ID", "").strip()


def fetch_sheet_data():
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
        with urllib.request.urlopen(req, timeout=10) as response:
            csv_string_data = response.read().decode("utf-8")

        csv_reader = csv.reader(io.StringIO(csv_string_data))
        raw_rows = list(csv_reader)

        if len(raw_rows) < 4:
            return [], []

        headers = [h.strip() for h in raw_rows[3]]
        data_rows = raw_rows[4:] if len(raw_rows) > 4 else []
        cleaned_rows = [r for r in data_rows if any(cell.strip() for cell in r)]

        return headers, cleaned_rows
    except Exception as e:
        print(f"[Error] Sheet fetch failed: {e}")
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
    """
    Explicit timing inference:
    - Part-time sat. Night -> To Camp: Saturday | To NYC: Sunday (Lord's Day)
    - Part-time fri. Night -> To Camp: Friday   | To NYC: Saturday
    - Day-only / offsite   -> To Camp: Saturday | To NYC: Saturday
    - Full time            -> To Camp: Friday   | To NYC: Sunday (Lord's Day)
    """
    status = status_val.lower().strip()
    
    # 1. Part-time (Sat. Night) -> Saturday arrival, Sunday return
    if "sat" in status and ("night" in status or "part-time" in status or "part time" in status or "overnight" in status):
        return "Saturday", "Sunday (Lord's Day)"
        
    # 2. Part-time (Fri. Night) -> Friday arrival, Saturday return
    elif "fri" in status and ("night" in status or "part-time" in status or "part time" in status or "overnight" in status):
        return "Friday", "Saturday"
        
    # 3. Day-only / Offsite -> Saturday arrival, Saturday return
    elif "day-only" in status or "day only" in status or "offsite" in status or "1 day" in status or "(1 day)" in status:
        return "Saturday", "Saturday"
        
    # 4. Full time / All weekend -> Friday arrival, Sunday return
    elif "full time" in status or "full-time" in status or "all weekend" in status or "full" in status:
        return "Friday", "Sunday (Lord's Day)"
        
    # 5. Fallbacks for specific day mentions
    elif "friday" in status or "fri" in status:
        return "Friday", "Friday (Day Only)"
    elif "sunday" in status or "lord's day" in status or "lords day" in status:
        return "Sunday (Lord's Day)", "Sunday (Lord's Day)"
    elif "saturday" in status or "sat" in status:
        return "Saturday", "Saturday"
        
    return "Unknown Timing", "Unknown Timing"


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Camp Comm Center</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { 
            background-color: #f2f2f7; 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; 
            color: #1c1c1e;
        }
        .sticky-top-panel { 
            position: sticky; 
            top: 0; 
            z-index: 1000; 
            background-color: #ffffff; 
            border-bottom: 1px solid #d1d1d6; 
            padding: 10px; 
        }
        .btn-segment {
            font-weight: 600;
            font-size: 0.85rem;
            padding: 6px 10px;
        }
        .card-header-g1 { 
            font-weight: bold; 
            padding: 7px 12px; 
            margin-top: 12px; 
            border-radius: 6px 6px 0 0; 
            font-size: 0.95rem;
            background-color: #e5e9f0;
            color: #1f2d3d;
        }
        .card-header-g2 { 
            font-weight: 600; 
            padding: 5px 12px; 
            border-bottom: 1px solid #e5e5ea;
            font-size: 0.85rem;
            background-color: #f4f6f9;
            color: #4a5568;
        }
        .card-header-surplus {
            background-color: #e6ffed !important;
            color: #22863a !important;
            border: 1px solid #b4f1c5;
        }
        .card-header-deficit {
            background-color: #ffe5e5 !important;
            color: #d73a49 !important;
            border: 1px solid #f8b4b4;
        }
        .badge-req { color: #d73a49; font-weight: 700; font-size: 0.82rem; }
        .badge-driver { color: #28a745; font-weight: 700; font-size: 0.82rem; }
        .badge-offer { color: #007aff; font-weight: 700; font-size: 0.82rem; }
        .list-group-item { 
            cursor: pointer; 
            transition: background-color 0.15s;
            border-color: #e5e5ea;
        }
        .list-group-item:active { background-color: #e5e5ea; }
        .dense-row { padding: 6px 12px; }
        .hall-checkbox-item { cursor: pointer; }
    </style>
</head>
<body>

<div class="sticky-top-panel shadow-sm">
    <div class="container-fluid px-1">
        <!-- Row 1: Segment Controller -->
        <div class="btn-group w-100 mb-2 shadow-none" role="group">
            <input type="radio" class="btn-check" name="viewMode" id="vm0" value="0" checked onchange="renderApp()">
            <label class="btn btn-outline-primary btn-segment" for="vm0">Classic</label>

            <input type="radio" class="btn-check" name="viewMode" id="vm1" value="1" onchange="renderApp()">
            <label class="btn btn-outline-primary btn-segment" for="vm1">Roster</label>

            <input type="radio" class="btn-check" name="viewMode" id="vm2" value="2" onchange="renderApp()">
            <label class="btn btn-outline-primary btn-segment" for="vm2">To Camp 🚐</label>

            <input type="radio" class="btn-check" name="viewMode" id="vm3" value="3" onchange="renderApp()">
            <label class="btn btn-outline-primary btn-segment" for="vm3">To NYC 🚐</label>
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

        <!-- Row 3: Settings and Refresh -->
        <div class="row g-2 mb-2">
            <div class="col-10">
                <button class="btn btn-secondary btn-sm w-100 fw-bold" style="background-color: #5856d6; border-color: #5856d6;" data-bs-toggle="modal" data-bs-target="#settingsModal">
                    ⚙️ Grouping & Sort Settings
                </button>
            </div>
            <div class="col-2">
                <button class="btn btn-success btn-sm w-100 fw-bold" onclick="refreshData()" title="Refresh Sheet Data">
                    🔄
                </button>
            </div>
        </div>

        <!-- Row 4: Search -->
        <div>
            <input type="text" id="searchInput" class="form-control form-control-sm" placeholder="Search attendees, rides, locations..." oninput="renderApp()">
        </div>
    </div>
</div>

<div class="container py-2">
    <div id="statusAlert" class="alert alert-warning d-none" role="alert"></div>
    <div id="contentList"></div>
</div>

<!-- Localities Multi-Select Modal -->
<div class="modal fade" id="hallsModal" tabindex="-1">
  <div class="modal-dialog modal-dialog-scrollable">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title fw-bold">Select Localities/Halls</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <div class="d-flex justify-content-between mb-3">
            <button class="btn btn-outline-secondary btn-sm" onclick="selectAllHalls(true)">Select All</button>
            <button class="btn btn-outline-secondary btn-sm" onclick="selectAllHalls(false)">Clear All</button>
        </div>
        <div id="hallsCheckboxList" class="list-group"></div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-primary w-100" data-bs-dismiss="modal" onclick="applyHallSelection()">Apply</button>
      </div>
    </div>
  </div>
</div>

<!-- Settings Modal -->
<div class="modal fade" id="settingsModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title fw-bold">⚙️ Dashboard Settings</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <div class="mb-3">
            <label class="form-label fw-bold small">Primary Group By (Classic & Roster)</label>
            <select id="selPrimaryGroup" class="form-select form-select-sm"></select>
        </div>
        <div class="mb-3">
            <label class="form-label fw-bold small">Secondary Group By (Classic)</label>
            <select id="selSecondaryGroup" class="form-select form-select-sm">
                <option value="__NONE__">(None / Disabled)</option>
            </select>
        </div>
        <div class="mb-3">
            <label class="form-label fw-bold small">Sort Column</label>
            <select id="selSortCol" class="form-select form-select-sm"></select>
        </div>
        <div class="mb-3">
            <label class="form-label fw-bold small">Sort Direction</label>
            <select id="selSortDir" class="form-select form-select-sm">
                <option value="asc">Ascending (A → Z)</option>
                <option value="desc">Descending (Z → A)</option>
            </select>
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-primary w-100" data-bs-dismiss="modal" onclick="saveSettings()">Save Changes</button>
      </div>
    </div>
  </div>
</div>

<!-- Details Modal -->
<div class="modal fade" id="detailModal" tabindex="-1">
  <div class="modal-dialog modal-dialog-centered modal-dialog-scrollable">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title fw-bold" id="modalTitle">Attendee Details</h5>
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
let allHalls = [];
let selectedHalls = new Set();
let transportFilter = "All";

let primaryGroupCol = "";
let secondaryGroupCol = "";
let sortCol = "";
let sortAscending = true;

async function refreshData() {
    let alertBox = document.getElementById('statusAlert');
    alertBox.classList.add('d-none');
    try {
        let res = await fetch('/api/data');
        let json = await res.json();
        rawData = json.rows || [];
        headers = json.headers || [];
        colMap = json.col_map || {};

        if (rawData.length === 0) {
            alertBox.innerText = "No data found. Ensure SHEET_ID is configured on Render and the sheet has link sharing set to 'Anyone with the link can view'.";
            alertBox.classList.remove('d-none');
            return;
        }

        // Initialize Halls
        let halls = new Set();
        rawData.forEach(r => {
            let h = (r.data[colMap.hall] || "").trim();
            if (h) halls.add(h);
        });
        allHalls = Array.from(halls).sort();
        if (allHalls.length === 0) allHalls = ["All Data"];
        selectedHalls = new Set(allHalls);

        primaryGroupCol = headers[colMap.hall] || headers[0] || "";
        sortCol = headers[colMap.name] || headers[0] || "";
        
        // Find default secondary column (camp stay/status)
        let secCandidate = headers.find(h => /camp stay|stay type|status|registration/i.test(h));
        secondaryGroupCol = secCandidate || "__NONE__";

        populateSettingsDropdowns();
        renderApp();
    } catch (err) {
        console.error(err);
        alertBox.innerText = "Error loading data. Check Render service logs.";
        alertBox.classList.remove('d-none');
    }
}

function populateSettingsDropdowns() {
    let selP = document.getElementById('selPrimaryGroup');
    let selS = document.getElementById('selSecondaryGroup');
    let selSort = document.getElementById('selSortCol');

    selP.innerHTML = "";
    selS.innerHTML = '<option value="__NONE__">(None / Disabled)</option>';
    selSort.innerHTML = "";

    headers.forEach(h => {
        if (!h.trim()) return;
        let opt1 = new Option(h, h, false, h === primaryGroupCol);
        let opt2 = new Option(h, h, false, h === secondaryGroupCol);
        let opt3 = new Option(h, h, false, h === sortCol);

        selP.add(opt1);
        selS.add(opt2);
        selSort.add(opt3);
    });
}

function saveSettings() {
    primaryGroupCol = document.getElementById('selPrimaryGroup').value;
    secondaryGroupCol = document.getElementById('selSecondaryGroup').value;
    sortCol = document.getElementById('selSortCol').value;
    sortAscending = (document.getElementById('selSortDir').value === "asc");
    renderApp();
}

function openHallsModal() {
    let list = document.getElementById('hallsCheckboxList');
    list.innerHTML = "";
    allHalls.forEach(h => {
        let isChecked = selectedHalls.has(h);
        let item = document.createElement('label');
        item.className = "list-group-item d-flex align-items-center hall-checkbox-item";
        item.innerHTML = `
            <input class="form-check-input me-2 hall-cb" type="checkbox" value="${h}" ${isChecked ? 'checked' : ''}>
            <span>${h}</span>
        `;
        list.appendChild(item);
    });
    new bootstrap.Modal(document.getElementById('hallsModal')).show();
}

function selectAllHalls(check) {
    document.querySelectorAll('.hall-cb').forEach(cb => cb.checked = check);
}

function applyHallSelection() {
    selectedHalls.clear();
    document.querySelectorAll('.hall-cb').forEach(cb => {
        if (cb.checked) selectedHalls.add(cb.value);
    });
    let btn = document.getElementById('btnHalls');
    if (selectedHalls.size === allHalls.length) btn.innerText = "📍 Halls: All";
    else if (selectedHalls.size === 0) btn.innerText = "📍 Halls: None";
    else btn.innerText = `📍 Halls: (${selectedHalls.size})`;
    renderApp();
}

function setTransportFilter(filterVal) {
    transportFilter = filterVal;
    let shortTitle = filterVal.replace(" Only", "");
    document.getElementById('btnTransport').innerText = `🚗 Trans: ${shortTitle}`;
    renderApp();
}

function renderApp() {
    let modeEl = document.querySelector('input[name="viewMode"]:checked');
    let mode = modeEl ? parseInt(modeEl.value) : 0;
    let query = (document.getElementById('searchInput').value || "").toLowerCase().trim();

    let pIdx = headers.indexOf(primaryGroupCol);
    let sIdx = secondaryGroupCol !== "__NONE__" ? headers.indexOf(secondaryGroupCol) : -1;
    let sortIdx = headers.indexOf(sortCol);
    if (sortIdx < 0) sortIdx = colMap.name;

    // 1. FILTERING
    let filtered = rawData.filter(r => {
        let hallVal = (r.data[colMap.hall] || "").trim();
        if (colMap.hall >= 0 && hallVal && !selectedHalls.has(hallVal)) return false;

        let tState = r._tstate;
        if (transportFilter === "Drivers Only" && !(tState.is_driver || tState.has_valid_space)) return false;
        if (transportFilter === "Ride Requests Only" && !tState.is_ride_req) return false;

        if (query && !r.data.some(cell => String(cell).toLowerCase().includes(query))) {
            return false;
        }
        return true;
    });

    // 2. SORTING
    const timeWeights = { "Friday": 1, "Saturday": 2, "Saturday (Day Only)": 3, "Sunday (Lord's Day)": 4, "Unknown Timing": 5 };

    filtered.sort((a, b) => {
        let aVal = (a.data[sortIdx] || "").toString().toLowerCase();
        let bVal = (b.data[sortIdx] || "").toString().toLowerCase();

        if (mode === 2 || mode === 3) {
            let tA = mode === 2 ? a._arrive : a._depart;
            let tB = mode === 2 ? b._arrive : b._depart;
            let wA = timeWeights[tA] || 99;
            let wB = timeWeights[tB] || 99;
            if (wA !== wB) return wA - wB;
            return sortAscending ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
        } else {
            let aP = pIdx >= 0 ? (a.data[pIdx] || "").toString().toLowerCase() : "";
            let bP = pIdx >= 0 ? (b.data[pIdx] || "").toString().toLowerCase() : "";
            if (aP !== bP) {
                return sortAscending ? aP.localeCompare(bP) : bP.localeCompare(aP);
            }
            if (sIdx >= 0) {
                let aS = (a.data[sIdx] || "").toString().toLowerCase();
                let bS = (b.data[sIdx] || "").toString().toLowerCase();
                if (aS !== bS) return sortAscending ? aS.localeCompare(bS) : bS.localeCompare(aS);
            }
            return aVal.localeCompare(bVal);
        }
    });

    // 3. PRECOMPUTE COUNTS PER GROUP
    let primaryCounts = {};
    let secondaryCounts = {};
    filtered.forEach(r => {
        let valG1 = pIdx >= 0 ? (r.data[pIdx] || "(Empty)").trim() : "All Attendees";
        let valG2 = sIdx >= 0 ? (r.data[sIdx] || "").trim() : "";
        
        primaryCounts[valG1] = (primaryCounts[valG1] || 0) + 1;
        if (valG2) {
            let compositeKey = valG1 + "|||" + valG2;
            secondaryCounts[compositeKey] = (secondaryCounts[compositeKey] || 0) + 1;
        }
    });

    // 4. RENDERING
    let container = document.getElementById('contentList');
    container.innerHTML = "";

    if (filtered.length === 0) {
        container.innerHTML = `<div class="text-center text-muted my-4">No records found matching filters.</div>`;
        return;
    }

    if (mode === 0 || mode === 1) {
        // Classic (0) or Dense Roster (1)
        let curG1 = null;
        let curG2 = null;
        let currentList = null;

        filtered.forEach(r => {
            let valG1 = pIdx >= 0 ? (r.data[pIdx] || "(Empty)").trim() : "All Attendees";
            let valG2 = sIdx >= 0 ? (r.data[sIdx] || "").trim() : "";

            if (valG1 !== curG1) {
                curG1 = valG1;
                curG2 = null;
                let g1Total = primaryCounts[valG1] || 0;

                let h1 = document.createElement('div');
                h1.className = "card-header-g1 shadow-sm d-flex justify-content-between align-items-center";
                h1.innerHTML = `
                    <span>📍 ${primaryGroupCol}: ${valG1}</span>
                    <span class="badge bg-secondary rounded-pill">${g1Total}</span>
                `;
                container.appendChild(h1);

                currentList = document.createElement('div');
                currentList.className = "list-group shadow-sm mb-3";
                container.appendChild(currentList);
            }

            if (sIdx >= 0 && valG2 && valG2 !== curG2) {
                curG2 = valG2;
                let g2Total = secondaryCounts[valG1 + "|||" + valG2] || 0;

                let h2 = document.createElement('div');
                h2.className = "card-header-g2 d-flex justify-content-between align-items-center";
                h2.innerHTML = `
                    <span>⛺ ${secondaryGroupCol}: ${valG2}</span>
                    <span class="badge bg-light text-dark border">${g2Total}</span>
                `;
                currentList.appendChild(h2);
            }

            let item = document.createElement('a');
            item.className = "list-group-item list-group-item-action";
            item.onclick = () => showDetail(r.data);

            let name = r.data[colMap.name] || "(Unnamed)";
            let status = colMap.status >= 0 ? (r.data[colMap.status] || "") : "";

            if (mode === 1) {
                // Dense Mode
                item.className += " dense-row d-flex justify-content-between align-items-center";
                item.innerHTML = `
                    <span class="fw-bold fs-6">${name}</span>
                    <small class="text-muted text-end text-truncate ms-2">${status}</small>
                `;
            } else {
                // Classic Mode
                let subParts = [];
                let tState = r._tstate;
                if (tState.is_driver) {
                    subParts.push("🚗 Driver");
                    if (tState.has_valid_space) subParts.push("💺 Seats: " + tState.space_val);
                } else if (tState.has_valid_space && tState.explicit_driver_no) {
                    subParts.push("💺 Offered Seats: " + tState.space_val);
                }
                if (tState.is_ride_req) subParts.push("🙋 Ride Req");

                let subText = subParts.length ? "  •  " + subParts.join("  •  ") : "";
                let statusText = status ? `[${status}]` : "";

                item.innerHTML = `
                    <div class="d-flex justify-content-between align-items-center">
                        <div>
                            <div class="fw-bold">${name}</div>
                            <small class="text-muted">${statusText} ${subText}</small>
                        </div>
                        <span class="text-muted">&rsaquo;</span>
                    </div>
                `;
            }
            currentList.appendChild(item);
        });

    } else {
        // Logistics Mode (2: To Camp, 3: To NYC)
        let groupMath = {};
        filtered.forEach(r => {
            let grp = mode === 2 ? r._arrive : r._depart;
            if (!groupMath[grp]) groupMath[grp] = { total: 0, needs: 0, seats: 0, rows: [] };
            groupMath[grp].total++;
            if (r._tstate.is_ride_req) groupMath[grp].needs++;
            if (r._tstate.is_driver) groupMath[grp].seats += r._tstate.seat_count;
            groupMath[grp].rows.push(r);
        });

        // Ensure chronological display order
        let sortedGroups = Object.keys(groupMath).sort((a, b) => {
            return (timeWeights[a] || 99) - (timeWeights[b] || 99);
        });

        sortedGroups.forEach(grp => {
            let stats = groupMath[grp];
            let deficit = stats.needs - stats.seats;
            let prefix = mode === 2 ? "🚐 To Camp:" : "🚐 To NYC:";

            let badgeHtml = "";
            let headerClass = "card-header-g1";

            if (stats.needs === 0 && stats.seats === 0) {
                badgeHtml = `<span class="badge bg-secondary">No Requests</span>`;
            } else if (deficit > 0) {
                headerClass += " card-header-deficit";
                badgeHtml = `<span class="badge bg-danger">⚠️ Short ${deficit}</span>`;
            } else {
                headerClass += " card-header-surplus";
                badgeHtml = `<span class="badge bg-success">✅ Surplus ${-deficit}</span>`;
            }

            let h = document.createElement('div');
            h.className = headerClass + " d-flex justify-content-between align-items-center";
            h.innerHTML = `
                <div>
                    <div><strong>🕒 ${prefix} ${grp}</strong> <span class="badge bg-dark bg-opacity-50 ms-1">${stats.total} attendees</span></div>
                    <small>Needs: <strong>${stats.needs}</strong> | Seats: <strong>${stats.seats}</strong></small>
                </div>
                <div>${badgeHtml}</div>
            `;
            container.appendChild(h);

            let list = document.createElement('div');
            list.className = "list-group shadow-sm mb-3";

            stats.rows.forEach(r => {
                let item = document.createElement('a');
                item.className = "list-group-item list-group-item-action d-flex justify-content-between align-items-center";
                item.onclick = () => showDetail(r.data);

                let badge = "";
                if (r._tstate.is_driver) badge = `<span class="badge-driver">Driver (${r._tstate.space_val})</span>`;
                else if (r._tstate.has_valid_space && r._tstate.explicit_driver_no) badge = `<span class="badge-offer">Offer (${r._tstate.space_val})</span>`;
                else if (r._tstate.is_ride_req) badge = `<span class="badge-req">Need Ride</span>`;

                let name = r.data[colMap.name] || "(Unnamed)";
                let status = colMap.status >= 0 ? (r.data[colMap.status] || "") : "";

                item.innerHTML = `
                    <div>
                        <div class="fw-bold">${name}</div>
                        <small class="text-muted">${status}</small>
                    </div>
                    <div>${badge}</div>
                `;
                list.appendChild(item);
            });
            container.appendChild(list);
        });
    }
}

function showDetail(row) {
    document.getElementById('modalTitle').innerText = row[colMap.name] || "Attendee Details";
    let body = document.getElementById('modalBody');
    body.innerHTML = "";
    headers.forEach((h, i) => {
        let cleanH = (h || "").trim();
        let cleanV = (row[i] || "").trim();
        if (cleanH && cleanV) {
            body.innerHTML += `
                <div class="mb-2 pb-1 border-bottom">
                    <small class="text-muted d-block fw-semibold">${cleanH}</small>
                    <span class="fs-6">${cleanV}</span>
                </div>
            `;
        }
    });
    new bootstrap.Modal(document.getElementById('detailModal')).show();
}

// Initial data load on startup
refreshData();
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

    col_name = find_col_idx(
        headers, ["full name", "name", "attendee", "participant"], 0
    )
    col_hall = find_col_idx(
        headers, ["locality", "hall", "locality/hall", "church"], -1
    )
    col_status = find_col_idx(
        headers,
        [
            "camp stay",
            "stay type",
            "status",
            "registration type",
            "attending",
            "full time",
            "registration",
        ],
        -1,
    )
    col_ride = find_col_idx(
        headers, ["need a ride", "ride request", "need ride", "passenger"], -1
    )
    col_drive = find_col_idx(
        headers, ["give rides", "driver", "can you drive", "can you give"], -1
    )
    col_space = find_col_idx(
        headers,
        ["space", "capacity", "seats", "how much space", "vehicle"],
        -1,
    )

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

    return jsonify(
        {
            "headers": headers,
            "rows": processed_rows,
            "col_map": {
                "name": col_name,
                "hall": col_hall,
                "status": col_status,
            },
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)