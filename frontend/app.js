const API_BASE = location.protocol === "file:" ? "http://127.0.0.1:8000" : "";

const map = L.map("map").setView([50.675, 17.921], 14);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a>",
}).addTo(map);

let startMarker = null;
let endMarker = null;
let greenLayer = null;
let shortLayer = null;
let zonesLayer = null;
let currentRequest = null;

const el = (id) => document.getElementById(id);
const hint = el("hint");

// Inicjalizacja: granice obszaru + strefy
async function init() {
  try {
    const info = await (await fetch(`${API_BASE}/info`)).json();
    const bounds = L.latLngBounds(info.bounds);
    map.fitBounds(bounds);
    
    map.setMinZoom(6); // Pozwala oddalić mapę na całą Polskę

    const zones = await (await fetch(`${API_BASE}/zones`)).json();
    zonesLayer = L.geoJSON(zones, {
      interactive: false,
      style: (f) =>
        f.properties.kind === "green"
          ? { color: "#2e7d32", weight: 1, fillColor: "#4caf50", fillOpacity: 0.3 }
          : { color: "#c62828", weight: 1, fillColor: "#e53935", fillOpacity: 0.25 },
    });
    if (el("showZones").checked) zonesLayer.addTo(map);
  } catch (err) {
    showError("Nie można połączyć się z backendem. Czy serwer działa na http://127.0.0.1:8000 ?");
  }
}

// Markery
function makeMarker(latlng, cls) {
  const icon = L.divIcon({ className: "", html: `<div class="pin ${cls}"></div>`, iconSize: [22, 22], iconAnchor: [11, 11] });
  const marker = L.marker(latlng, { icon, draggable: true }).addTo(map);
  marker.on("dragend", () => {
    if (startMarker && endMarker) requestRoute();
  });
  return marker;
}

map.on("click", (e) => {
  clearError();
  if (!startMarker) {
    startMarker = makeMarker(e.latlng, "start");
    hint.innerHTML = "Teraz kliknij punkt <b>końca</b>.";
  } else if (!endMarker) {
    endMarker = makeMarker(e.latlng, "end");
    hint.innerHTML = "Przeciągnij markery lub zmień suwak, aby przeliczyć trasę.";
    requestRoute();
  } else {
    resetAll();
    startMarker = makeMarker(e.latlng, "start");
    hint.innerHTML = "Teraz kliknij punkt <b>końca</b>.";
  }
});

function resetAll() {
  [startMarker, endMarker, greenLayer, shortLayer].forEach((l) => l && map.removeLayer(l));
  startMarker = endMarker = greenLayer = shortLayer = null;
  el("stats").hidden = true;
  clearError();
  hint.innerHTML = "Kliknij na mapie punkt <b>startu</b>.";
}

// Zapytanie do API
async function requestRoute() {
  if (!startMarker || !endMarker) return;
  if (currentRequest) currentRequest.abort();
  currentRequest = new AbortController();

  const s = startMarker.getLatLng();
  const t = endMarker.getLatLng();
  const params = new URLSearchParams({
    start_lat: s.lat,
    start_lon: s.lng,
    end_lat: t.lat,
    end_lon: t.lng,
    green: (el("green").value / 100).toString(),
  });

  el("loading").hidden = false;
  clearError();

  try {
    const res = await fetch(`${API_BASE}/route?${params}`, { signal: currentRequest.signal });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "Błąd serwera");
    drawRoutes(body);
  } catch (err) {
    if (err.name !== "AbortError") showError(err.message);
  } finally {
    el("loading").hidden = true;
  }
}

function drawRoutes(fc) {
  [greenLayer, shortLayer].forEach((l) => l && map.removeLayer(l));

  const green = fc.features.find((f) => f.properties.kind === "green");
  const shortest = fc.features.find((f) => f.properties.kind === "shortest");

  shortLayer = L.geoJSON(shortest, {
    style: { color: "#777", weight: 4, opacity: 0.9, dashArray: "8 8" },
  }).addTo(map);
  greenLayer = L.geoJSON(green, {
    style: { color: "#1b9e3a", weight: 6, opacity: 0.95 },
  }).addTo(map);

  map.fitBounds(greenLayer.getBounds().pad(0.2));
  showStats(green.properties, shortest.properties);
}

// Statystyki
const pct = (x) => `${Math.round(x * 100)}%`;
const km = (m) => `${(m / 1000).toFixed(2)} km`;

function showStats(g, s) {
  el("gLen").textContent = km(g.length_m);
  el("sLen").textContent = km(s.length_m);
  el("gTime").textContent = `${g.minutes} min`;
  el("sTime").textContent = `${s.minutes} min`;
  el("gGreen").textContent = pct(g.green_share);
  el("sGreen").textContent = pct(s.green_share);
  el("gNoise").textContent = pct(g.noise_share);
  el("sNoise").textContent = pct(s.noise_share);

  const extra = ((g.length_m - s.length_m) / s.length_m) * 100;
  el("detour").textContent =
    extra < 0.5
      ? "Zielona trasa pokrywa się z najkrótszą."
      : `Zielona trasa jest dłuższa o ${extra.toFixed(0)}% (${Math.round(g.length_m - s.length_m)} m).`;
  el("stats").hidden = false;
}

// Kontrolki
let sliderTimer = null;
el("green").addEventListener("input", (e) => {
  el("greenValue").textContent = `${e.target.value}%`;
  clearTimeout(sliderTimer);
  sliderTimer = setTimeout(requestRoute, 250);
});

el("showZones").addEventListener("change", (e) => {
  if (!zonesLayer) return;
  e.target.checked ? zonesLayer.addTo(map) : map.removeLayer(zonesLayer);
});

el("reset").addEventListener("click", resetAll);

function showError(msg) {
  el("error").textContent = msg;
  el("error").hidden = false;
}
function clearError() {
  el("error").hidden = true;
}

init();