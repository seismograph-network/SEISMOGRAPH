"use strict";
// SEISMOGRAPH dashboard — vanilla JS, no dependencies.
// Polls GET /v1/weather every POLL_MS ms and renders model status cards.
//
// SG-TRACE: REQ-DASH-001
//   assumption: /v1/weather returns list[ModelWeatherResponse] as JSON
//   test: test_weather_returns_stable_when_no_alerts (backend)

const POLL_MS = 60_000;

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtTokens(val) {
  if (val === null || val === undefined) return "—";
  return Math.round(Number(val)).toLocaleString() + " tok";
}

function fmtRate(val) {
  if (val === null || val === undefined) return "—";
  return (Number(val) * 100).toFixed(1) + "%";
}

function fmtTimestamp(ts) {
  if (!ts) return "—";
  try {
    // Backend returns naive UTC; append Z so Date parses as UTC.
    const normalized = ts.endsWith("Z") || ts.includes("+") ? ts : ts + "Z";
    return new Date(normalized).toLocaleString(undefined, {
      dateStyle: "short",
      timeStyle: "medium",
    });
  } catch (_) {
    return String(ts);
  }
}

// ---------------------------------------------------------------------------
// DOM builders
// ---------------------------------------------------------------------------

function buildCard(entry) {
  const drifting = entry.status === "DRIFTING";
  const card = document.createElement("div");
  card.className = "card " + (drifting ? "card-drifting" : "card-stable");

  // Header row
  const top = document.createElement("div");
  top.className = "card-top";

  const dot = document.createElement("span");
  dot.className = "dot " + (drifting ? "dot-drifting" : "dot-stable");

  const name = document.createElement("span");
  name.className = "model-name";
  name.textContent = entry.model_tuple;

  const badge = document.createElement("span");
  badge.className = "badge " + (drifting ? "badge-drifting" : "badge-stable");
  badge.textContent = entry.status;

  top.appendChild(dot);
  top.appendChild(name);
  top.appendChild(badge);

  // Metrics list
  const dl = document.createElement("dl");
  dl.className = "metrics";

  const rows = [
    ["Avg output length",  fmtTokens(entry.recent_avg_output_length), false],
    ["JSON success rate",  fmtRate(entry.recent_json_success_rate),   false],
  ];
  if (entry.last_alert_timestamp) {
    rows.push(["Last alert", fmtTimestamp(entry.last_alert_timestamp), true]);
  }

  rows.forEach(([label, value, isAlert]) => {
    const row = document.createElement("div");
    row.className = "metric-row" + (isAlert ? " alert-row" : "");

    const dt = document.createElement("dt");
    dt.textContent = label;

    const dd = document.createElement("dd");
    dd.textContent = value;

    row.appendChild(dt);
    row.appendChild(dd);
    dl.appendChild(row);
  });

  card.appendChild(top);
  card.appendChild(dl);
  return card;
}

function buildEmpty() {
  const p = document.createElement("p");
  p.className = "empty";
  p.innerHTML =
    "No probes reporting yet.<br>" +
    "Run a canary probe with <code>probe flush</code> to populate this dashboard.";
  return p;
}

// ---------------------------------------------------------------------------
// Fetch + render
// ---------------------------------------------------------------------------

async function fetchWeather() {
  const r = await fetch("/v1/weather");
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

function setError(msg) {
  const el = document.getElementById("status-banner");
  if (!el) return;
  el.textContent = "⚠️ " + msg;
  el.classList.add("visible");
}

function clearError() {
  const el = document.getElementById("status-banner");
  if (!el) return;
  el.textContent = "";
  el.classList.remove("visible");
}

function setLastUpdated() {
  const el = document.getElementById("last-updated");
  if (!el) return;
  el.textContent = "Updated " + new Date().toLocaleTimeString();
}

async function refresh() {
  const grid = document.getElementById("weather-grid");
  if (!grid) return;
  try {
    const data = await fetchWeather();
    clearError();
    grid.innerHTML = "";
    if (!Array.isArray(data) || data.length === 0) {
      grid.appendChild(buildEmpty());
    } else {
      data.forEach((entry) => grid.appendChild(buildCard(entry)));
    }
    setLastUpdated();
  } catch (err) {
    setError("Could not reach gateway: " + err.message);
  }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  refresh();
  setInterval(refresh, POLL_MS);
});
