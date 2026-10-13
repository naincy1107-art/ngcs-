// ============ CONNECTION STATE FLAG ============
// Set isConnected = true when a real serial/WebSocket port is open or SIM is running.
// All live-data updates check this flag before rendering.
let isConnected = false;

// ============ MISSION ELAPSED TIMER ============
// Starts counting from session load, runs forever regardless of connection state.
// Only a deliberate mission reset would set this back to 0.
const metStartTime = Date.now();

// ============ BACKEND API HELPER ============
function getApiUrl(path) {
  const origin = (location.protocol === 'file:' || !location.port || location.port !== '5000')
    ? 'http://localhost:5000'
    : '';
  return origin + (path.startsWith('/') ? path : '/' + path);
}

// ============ STATE ============
const state = {
  t: 0,
  simSpeed: 2,
  met: 0,
  rkt: { alt: 0, pres: 0, temp: 0, vel: 0, ax: 0, ay: 0, az: 0, gx: 0, gy: 0, gz: 0, pitch: 0, roll: 0, yaw: 0, lat: 26.4502, lon: 80.3322, gpsTime: 'N/A', hdop: 0, fix: 'N/A', sats: 0, gpsValid: false, voltage: 0, state: 'BOOT', packetCount: 0, timeStamping: 0, gpsAlt: 0 },
  cs: { alt: 0, pres: 0, temp: 0, vel: 0, ax: 0, ay: 0, az: 0, gx: 0, gy: 0, gz: 0, pitch: 0, roll: 0, yaw: 0, lat: 26.4499, lon: 80.3319, gpsTime: 'N/A', hdop: 0, fix: 'N/A', sats: 0, gpsValid: false, voltage: 0, state: 'BOOT', packetCount: 0, timeStamping: 0, gpsAlt: 0 },
  ground: { lat: 26.4497, lon: 80.3315 },
};

let simInterval = null;
let chartT0 = null;                      // wall-clock origin of the live chart time axis
const lastChartPc = { rkt: -1, cs: -1 };  // last packet counts plotted

// ============ ML - TERRAIN CLASSIFICATION ============
let terrainSelectedFile = null;
let terrainUploading = false;

// ============ MAP ============
let leafletMap = null, rktMarker = null, csMarker = null, gndMarker = null, rktPolyline = null, csPolyline = null;
let rktTrack = [], csTrack = [];
const mapEl = document.getElementById('leaflet-map');
const hasLeaflet = typeof L !== 'undefined';
let usingLeaflet = false;
let leafletTilesReady = false;

function haversine(lat1, lon1, lat2, lon2) {
  const R = 6371000, dLat = (lat2 - lat1) * Math.PI / 180, dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function fmtDist(d) { return d < 1000 ? d.toFixed(0) + ' m' : (d / 1000).toFixed(2) + ' km'; }

function makeIcon(color, symbol) {
  return L.divIcon({
    className: '',
    html: `<div style="width:22px;height:22px;border-radius:50%;background:${color};border:2px solid white;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:bold;color:#fff;">${symbol}</div>`,
    iconSize: [22, 22], iconAnchor: [11, 11], popupAnchor: [0, -12]
  });
}

function initLeafletMap() {
  usingLeaflet = true;
  leafletTilesReady = false;
  if (mapEl) mapEl.className = '';
  leafletMap = L.map('leaflet-map', { zoomControl: true, attributionControl: true }).setView([26.4499, 80.3319], 15);
  let tileErrors = 0;
  const baseLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 19,
    maxNativeZoom: 18,
    attribution: 'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics'
  }).addTo(leafletMap);
  baseLayer.on('tileerror', () => {
    tileErrors++;
    if (tileErrors >= 3 && usingLeaflet) switchToOfflineMap();
  });
  baseLayer.on('tileload', () => { leafletTilesReady = true; });
  setTimeout(() => {
    if (usingLeaflet && !leafletTilesReady) switchToOfflineMap();
  }, 6000);
  setTimeout(() => { if (leafletMap) leafletMap.invalidateSize(); }, 250);

  rktMarker = L.marker([state.rkt.lat, state.rkt.lon], { icon: makeIcon('#00f5ff', 'R') })
    .bindPopup('<b style="color:#00f5ff">ROCKET</b><br>Awaiting telemetry...');
  csMarker = L.marker([state.cs.lat, state.cs.lon], { icon: makeIcon('#00ff88', 'C') })
    .bindPopup('<b style="color:#00ff88">CANSAT</b><br>Awaiting telemetry...');

  gndMarker = L.marker([state.ground.lat, state.ground.lon], {
    icon: L.divIcon({
      className: '',
      html: `<div style="width:18px;height:18px;background:#d5a400;border:2px solid #fff;border-radius:3px;transform:rotate(45deg);"></div>`,
      iconSize: [18, 18], iconAnchor: [9, 9]
    })
  }).addTo(leafletMap).bindPopup('<b style="color:#ffe600">GROUND STATION</b>');
  rktPolyline = L.polyline([], { color: '#00f5ff', weight: 2, dashArray: '5,4', opacity: 0.8 }).addTo(leafletMap);
  csPolyline = L.polyline([], { color: '#00ff88', weight: 2, dashArray: '4,5', opacity: 0.8 }).addTo(leafletMap);

  // Recenter control: fit the view to every marker currently on the map
  const recenterMap = () => {
    if (!leafletMap) return;
    const pts = [gndMarker, rktMarker, csMarker]
      .filter(m => m && leafletMap.hasLayer(m))
      .map(m => m.getLatLng());
    if (pts.length > 1) leafletMap.fitBounds(L.latLngBounds(pts), { padding: [40, 40], maxZoom: 17 });
    else if (pts.length === 1) leafletMap.setView(pts[0], 16);
  };
  const RecenterControl = L.Control.extend({
    options: { position: 'topleft' },
    onAdd: function () {
      const box = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
      const a = L.DomUtil.create('a', '', box);
      a.href = '#'; a.title = 'Recenter map'; a.setAttribute('role', 'button');
      a.innerHTML = '&#9678;';
      a.style.fontSize = '18px';
      L.DomEvent.on(a, 'click', e => { L.DomEvent.stop(e); recenterMap(); });
      L.DomEvent.disableClickPropagation(box);
      return box;
    }
  });
  leafletMap.addControl(new RecenterControl());
}

function initOfflineMap() {
  usingLeaflet = false;
  if (!mapEl) return;
  mapEl.className = 'offline-map';
  mapEl.innerHTML = `
    <div class="offline-road r1"></div>
    <div class="offline-road r2"></div>
    <div class="offline-road r3"></div>
    <svg class="offline-track" viewBox="0 0 100 100" preserveAspectRatio="none">
      <polyline id="offline-rkt-track" points="" fill="none" stroke="#00f5ff" stroke-width="0.7" stroke-dasharray="2 1" opacity="0.9"/>
      <polyline id="offline-cs-track" points="" fill="none" stroke="#00ff88" stroke-width="0.7" stroke-dasharray="1.6 1" opacity="0.9"/>
    </svg>
    <div class="offline-marker rocket" id="offline-rkt">R</div><div class="offline-label" id="offline-rkt-label"></div>
    <div class="offline-marker cansat" id="offline-cs">C</div><div class="offline-label" id="offline-cs-label"></div>
    <div class="offline-marker ground" id="offline-ground"><span>G</span></div><div class="offline-label" id="offline-ground-label">GROUND</div>
    <div class="offline-coords" id="offline-coords"></div>
  `;
}

function switchToOfflineMap() {
  if (leafletMap) {
    try { leafletMap.remove(); } catch (err) { }
    leafletMap = null;
  }
  initOfflineMap();
  updateOfflineMap();
}

function projectOfflinePoint(lat, lon) {
  const points = [...rktTrack, ...csTrack, [state.ground.lat, state.ground.lon], [state.rkt.lat, state.rkt.lon], [state.cs.lat, state.cs.lon]];
  const lats = points.map(p => p[0]), lons = points.map(p => p[1]);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats), minLon = Math.min(...lons), maxLon = Math.max(...lons);
  const latSpan = Math.max(maxLat - minLat, 0.0008);
  const lonSpan = Math.max(maxLon - minLon, 0.0008);
  const x = 10 + ((lon - (minLon + maxLon - lonSpan) / 2) / lonSpan) * 80;
  const y = 90 - ((lat - (minLat + maxLat - latSpan) / 2) / latSpan) * 80;
  return [Math.max(5, Math.min(95, x)), Math.max(5, Math.min(95, y))];
}

function setOfflinePosition(id, labelId, lat, lon, label) {
  const marker = document.getElementById(id);
  const labelEl = document.getElementById(labelId);
  const [x, y] = projectOfflinePoint(lat, lon);
  if (marker) { marker.style.left = `${x}%`; marker.style.top = `${y}%`; }
  if (labelEl) { labelEl.style.left = `${x}%`; labelEl.style.top = `${y}%`; labelEl.textContent = label; }
}

function updateOfflineMap() {
  const r = state.rkt, c = state.cs, g = state.ground;
  setOfflinePosition('offline-rkt', 'offline-rkt-label', r.lat, r.lon, `R ${r.alt.toFixed(0)}m`);
  setOfflinePosition('offline-cs', 'offline-cs-label', c.lat, c.lon, `C ${c.alt.toFixed(0)}m`);
  setOfflinePosition('offline-ground', 'offline-ground-label', g.lat, g.lon, 'GROUND');
  const rktLine = document.getElementById('offline-rkt-track');
  const csLine = document.getElementById('offline-cs-track');
  if (rktLine) rktLine.setAttribute('points', rktTrack.map(([lat, lon]) => projectOfflinePoint(lat, lon).join(',')).join(' '));
  if (csLine) csLine.setAttribute('points', csTrack.map(([lat, lon]) => projectOfflinePoint(lat, lon).join(',')).join(' '));
  const coords = document.getElementById('offline-coords');
  if (coords) coords.innerHTML = `RKT ${r.lat.toFixed(5)}, ${r.lon.toFixed(5)}<br>CS&nbsp;&nbsp;${c.lat.toFixed(5)}, ${c.lon.toFixed(5)}`;
}

try {
  if (hasLeaflet) initLeafletMap();
  else initOfflineMap();
} catch (err) {
  initOfflineMap();
}

function setMapIdleOverlay(show) {
  const mapArea = document.querySelector('.map-area');
  if (!mapArea) return;
  let overlay = mapArea.querySelector('.idle-marker-overlay');
  if (show) {
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.className = 'idle-marker-overlay';
      overlay.innerHTML = `
        <div class="overlay-icon">&#9711;</div>
        <div class="overlay-line1">No position data</div>
        <div class="overlay-line2">awaiting signal&hellip;</div>
      `;
      mapArea.style.position = 'relative';
      mapArea.appendChild(overlay);
    }
  } else {
    if (overlay) overlay.remove();
  }
}

function setLeafletMarkersVisible(show) {
  if (!usingLeaflet || !leafletMap) return;
  if (show) {
    if (rktMarker && !leafletMap.hasLayer(rktMarker)) rktMarker.addTo(leafletMap);
    if (csMarker && !leafletMap.hasLayer(csMarker)) csMarker.addTo(leafletMap);
  } else {
    if (rktMarker && leafletMap.hasLayer(rktMarker)) rktMarker.remove();
    if (csMarker && leafletMap.hasLayer(csMarker)) csMarker.remove();
  }
}

function setOfflineMarkersVisible(show) {
  const ids = ['offline-rkt', 'offline-rkt-label', 'offline-cs', 'offline-cs-label'];
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = show ? '' : 'none';
  });
}

function updateMap() {
  if (!isConnected) {
    document.getElementById('dist-cs-rkt').textContent = '--';
    document.getElementById('dist-cs-gnd').textContent = '--';
    document.getElementById('dist-rkt-gnd').textContent = '--';
    setMapIdleOverlay(true);
    setLeafletMarkersVisible(false);
    setOfflineMarkersVisible(false);
    return;
  }
  setMapIdleOverlay(false);
  setLeafletMarkersVisible(true);
  setOfflineMarkersVisible(true);
  const r = state.rkt, c = state.cs, g = state.ground;

  rktTrack.push([r.lat, r.lon]);
  csTrack.push([c.lat, c.lon]);
  if (rktTrack.length > 200) rktTrack.shift();
  if (csTrack.length > 200) csTrack.shift();

  if (usingLeaflet) {
    rktMarker.setLatLng([r.lat, r.lon]);
    rktMarker.setPopupContent(`<b style="color:#00f5ff">ROCKET</b><br>LAT: ${r.lat.toFixed(5)}<br>LON: ${r.lon.toFixed(5)}<br>ALT: ${r.alt.toFixed(0)} m<br>VEL: ${r.vel.toFixed(0)} m/s`);
    csMarker.setLatLng([c.lat, c.lon]);
    csMarker.setPopupContent(`<b style="color:#00ff88">CANSAT</b><br>LAT: ${c.lat.toFixed(5)}<br>LON: ${c.lon.toFixed(5)}<br>ALT: ${c.alt.toFixed(0)} m<br>VEL: ${c.vel.toFixed(0)} m/s`);
    rktPolyline.setLatLngs(rktTrack);
    csPolyline.setLatLngs(csTrack);
  } else {
    updateOfflineMap();
  }

  document.getElementById('dist-cs-rkt').textContent = fmtDist(haversine(c.lat, c.lon, r.lat, r.lon));
  document.getElementById('dist-cs-gnd').textContent = fmtDist(haversine(c.lat, c.lon, g.lat, g.lon));
  document.getElementById('dist-rkt-gnd').textContent = fmtDist(haversine(r.lat, r.lon, g.lat, g.lon));
}

function setPhase(el) {
  document.querySelectorAll('.status-pill').forEach(p => p.className = 'status-pill inactive');
  el.className = 'status-pill active';
}

function updateClock() {
  const elapsed = Math.floor((Date.now() - metStartTime) / 1000);
  const h = Math.floor(elapsed / 3600);
  const m = Math.floor((elapsed % 3600) / 60);
  const s = elapsed % 60;
  const metEl = document.getElementById('met');
  if (metEl) {
    metEl.textContent = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    metEl.style.color = '';
  }
  refreshGnssClock();
}
setInterval(updateClock, 1000);

const INDIA_TZ = 'Asia/Kolkata';
const istTimeFormatter = new Intl.DateTimeFormat('en-IN', {
  timeZone: INDIA_TZ,
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

function currentUtcGpsCompact() {
  const now = new Date();
  return `${String(now.getUTCHours()).padStart(2, '0')}${String(now.getUTCMinutes()).padStart(2, '0')}${String(now.getUTCSeconds()).padStart(2, '0')}`;
}

function parseGpsTimeToUtcDate(value) {
  if (value === undefined || value === null || value === '') return null;
  const text = String(value).trim();
  if (text === 'N/A') return null;

  if (/^\d{6}(?:\.\d+)?$/.test(text)) {
    const h = Number(text.slice(0, 2));
    const m = Number(text.slice(2, 4));
    const s = Number(text.slice(4, 6));
    const now = new Date();
    return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h, m, s));
  }

  if (/^\d{2}:\d{2}:\d{2}$/.test(text)) {
    const [h, m, s] = text.split(':').map(Number);
    const now = new Date();
    return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h, m, s));
  }

  const parsed = Date.parse(text);
  return Number.isNaN(parsed) ? null : new Date(parsed);
}

function formatGpsTime(value) {
  if (value === undefined || value === null || value === '') return 'N/A';
  const text = String(value).trim();
  if (text === 'N/A' || text === '' || text === '0') return 'N/A';

  if (/^\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(text)) {
    const rawTime = text.split('.')[0];
    return rawTime.includes('IST') ? rawTime : `${rawTime} IST`;
  }

  const cleanNum = text.split('.')[0];
  if (/^\d{1,6}$/.test(cleanNum)) {
    const padded = cleanNum.padStart(6, '0');
    const hh = padded.slice(0, 2);
    const mm = padded.slice(2, 4);
    const ss = padded.slice(4, 6);
    return `${hh}:${mm}:${ss} IST`;
  }

  const date = parseGpsTimeToUtcDate(value);
  if (!date) return text.includes('IST') ? text : `${text} IST`;
  return `${istTimeFormatter.format(date)} IST`;
}

function refreshGnssClock() {
  const el = document.getElementById('clock');
  if (!el) return;

  let displayTime;
  if (isConnected) {
    const rkt = state.rkt;
    const cs = state.cs;
    if (rkt.gpsTime && rkt.gpsTime !== 'N/A') displayTime = formatGpsTime(rkt.gpsTime);
    else if (cs.gpsTime && cs.gpsTime !== 'N/A') displayTime = formatGpsTime(cs.gpsTime);
  }
  if (!displayTime || displayTime === 'N/A') {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    const ss = String(now.getSeconds()).padStart(2, '0');
    displayTime = `${hh}:${mm}:${ss} IST`;
  }

  el.textContent = displayTime;
  el.style.color = '';
}

function syncIndiaGpsTime() {
  const utc = currentUtcGpsCompact();
  state.rkt.gpsTime = utc;
  state.cs.gpsTime = utc;
  state.rkt.gpsValid = true;
  state.cs.gpsValid = true;
}

// ============ GAUGE SVG ============
function createGaugeSVG(container, cfg) {
  if (!container) return { update() {}, setIdle() {}, clearIdle() {} };
  const wrap = document.createElement('div');
  wrap.className = 'gauge-wrap';
  wrap.innerHTML = `
    <svg width="110" height="110" viewBox="0 0 110 110">
      <circle cx="55" cy="55" r="42" fill="#f7fbff" stroke="#c7d9ec" stroke-width="7"/>
      <circle id="arc-${cfg.id}" cx="55" cy="55" r="42" fill="none" stroke="${cfg.color}" stroke-width="7" stroke-dasharray="0 264" stroke-dashoffset="-66" stroke-linecap="round" style="transition:stroke-dasharray 0.4s ease"/>
      <text id="gval-${cfg.id}" x="55" y="50" text-anchor="middle" dominant-baseline="middle" class="gauge-val" fill="${cfg.color}" style="font-family:'Orbitron',sans-serif;font-size:15px;font-weight:700;">${cfg.val}</text>
      <text x="55" y="66" text-anchor="middle" class="gauge-unit" fill="${cfg.dimColor || '#4a6a80'}" style="font-family:'Share Tech Mono',monospace;font-size:8px;">${cfg.unit}</text>
    </svg>
    <div class="gauge-label" style="color:${cfg.color};font-size:0.58rem;">${cfg.label}</div>
    <div class="gauge-status stable" id="gstat-${cfg.id}">* STABLE</div>
  `;
  container.appendChild(wrap);
  return {
    update(val, max) {
      const pct = Math.min(val / max, 1), arc = pct * 264;
      const el = document.getElementById(`arc-${cfg.id}`);
      if (el) el.setAttribute('stroke-dasharray', `${arc} 264`);
      const ve = document.getElementById(`gval-${cfg.id}`);
      if (ve) ve.textContent = typeof val === 'number' ? val.toFixed(cfg.dec || 0) : val;
      const st = document.getElementById(`gstat-${cfg.id}`);
      if (st) { if (pct > 0.85) { st.textContent = '* CRITICAL'; st.className = 'gauge-status crit'; } else if (pct > 0.65) { st.textContent = '* WARNING'; st.className = 'gauge-status warn'; } else { st.textContent = '* STABLE'; st.className = 'gauge-status stable'; } }
    },
    setIdle() {
      const arc = document.getElementById(`arc-${cfg.id}`);
      if (arc) { arc.setAttribute('stroke-dasharray', '0 264'); arc.setAttribute('stroke', '#c7d9ec'); }
      const ve = document.getElementById(`gval-${cfg.id}`);
      if (ve) { ve.textContent = '--'; ve.setAttribute('fill', '#9aafc2'); }
      const st = document.getElementById(`gstat-${cfg.id}`);
      if (st) { st.textContent = 'NO SIGNAL'; st.className = 'gauge-status nosignal'; }
    },
    clearIdle() {
      const arc = document.getElementById(`arc-${cfg.id}`);
      if (arc) arc.setAttribute('stroke', cfg.color);
      const ve = document.getElementById(`gval-${cfg.id}`);
      if (ve) ve.setAttribute('fill', cfg.color);
    }
  };
}

const cgc = document.getElementById('cansat-gauges');
const rgc = document.getElementById('rocket-gauges');
const gauges = {
  csAlt: createGaugeSVG(cgc, { id: 'csAlt', label: 'HEIGHT', unit: 'm', color: '#00ff88', val: '0', dec: 0 }),
  csPres: createGaugeSVG(cgc, { id: 'csPres', label: 'PRESSURE', unit: 'hPa', color: '#00f5ff', val: '0', dec: 0 }),
  csTemp: createGaugeSVG(cgc, { id: 'csTemp', label: 'TEMP', unit: 'deg C', color: '#ff8c00', val: '0.0', dec: 1 }),
  csVel: createGaugeSVG(cgc, { id: 'csVel', label: 'SPEED', unit: 'm/s', color: '#0a6fff', val: '0', dec: 0 }),
  rktAlt: createGaugeSVG(rgc, { id: 'rktAlt', label: 'HEIGHT', unit: 'm', color: '#00f5ff', val: '0', dec: 0 }),
  rktPres: createGaugeSVG(rgc, { id: 'rktPres', label: 'PRESSURE', unit: 'hPa', color: '#00ff88', val: '0', dec: 0 }),
  rktTemp: createGaugeSVG(rgc, { id: 'rktTemp', label: 'TEMP', unit: 'deg C', color: '#ff8c00', val: '0.0', dec: 1 }),
  rktVel: createGaugeSVG(rgc, { id: 'rktVel', label: 'SPEED', unit: 'm/s', color: '#0a6fff', val: '0', dec: 0 }),
};

function setAwaitingTelemetry() {
  [
    'rkt-ax', 'rkt-ay', 'rkt-az', 'rkt-gx', 'rkt-gy', 'rkt-gz', 'rkt-gnss-time', 'rkt-lat', 'rkt-lon', 'rkt-gnss-alt', 'rkt-hdop', 'rkt-fix',
    'rkt-pitch', 'rkt-roll', 'rkt-yaw',
    'cs-ax', 'cs-ay', 'cs-az', 'cs-gx', 'cs-gy', 'cs-gz', 'cs-gnss-time', 'cs-lat', 'cs-lon', 'cs-gnss-alt', 'cs-hdop', 'cs-fix',
    'cs-pitch', 'cs-roll', 'cs-yaw',
    'rkt-voltage', 'rkt-packet-count', 'rkt-time-stamping', 'rkt-state',
    'cs-voltage', 'cs-packet-count', 'cs-time-stamping', 'cs-state'
  ].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = id.includes('gnss-time') ? 'N/A' : '--';
  });
  setText('rkt-sats', '-- SATS');
  setText('cs-sats', '-- SATS');
  ['rktAlt', 'rktPres', 'rktTemp', 'rktVel', 'csAlt', 'csPres', 'csTemp', 'csVel'].forEach(id => {
    if (gauges[id] && gauges[id].update) gauges[id].update(0, 1);
  });
}

function resetGpsTelemetry(role) {
  const target = state[role];
  if (!target) return;
  target.gpsTime = 'N/A';
  target.hdop = 0;
  target.fix = 'N/A';
  target.sats = 0;
  target.gpsValid = false;
  renderGpsTelemetry(role, target);
  refreshGnssClock();
}

// ============ CHART SETUP ============
const CHART_WINDOW_SEC = 10;
const CHART_MAX_POINTS = 200;
const CHART_COLORS = ['#00e5c8', '#4dd4ff'];

const chartNoiseProfile = {
  cmpTemp: [0.35, 0.28],
  cmpAlt: [1.8, 1.4],
  cmpPres: [0.6, 0.5],
  cmpVel: [2.2, 1.6],
};

const chartYLabels = {
  cmpTemp: 'Temperature (°C)',
  cmpAlt: 'Altitude (m)',
  cmpPres: 'Pressure (hPa)',
  cmpVel: 'Velocity (m/s)',
};

const darkChartBgPlugin = {
  id: 'darkChartBackground',
  beforeDraw(chart) {
    const { ctx, chartArea } = chart;
    if (!chartArea) return;
    ctx.save();
    ctx.fillStyle = '#0a0a0a';
    ctx.fillRect(chartArea.left, chartArea.top, chartArea.right - chartArea.left, chartArea.bottom - chartArea.top);
    ctx.restore();
  }
};

function sensorNoise(value, amplitude) {
  if (!Number.isFinite(value) || !amplitude) return value;
  return value + (Math.random() - 0.5) * amplitude * 2;
}

const chartDefaults = {
  animation: false,
  responsive: true,
  maintainAspectRatio: false,
  layout: { padding: { left: 8, right: 12, top: 12, bottom: 6 } },
  interaction: { intersect: false, mode: 'index' },
  elements: {
    line: {
      borderWidth: 2,
      tension: 0,
      borderJoinStyle: 'miter',
      capBezierPoints: false
    },
    point: { radius: 0, hoverRadius: 3 }
  },
  plugins: {
    legend: { display: false },
    tooltip: {
      backgroundColor: '#111111',
      titleColor: '#00e5c8',
      bodyColor: '#cccccc',
      borderColor: '#333333',
      borderWidth: 1,
      displayColors: true
    }
  },
  scales: {
    x: {
      type: 'linear',
      display: true,
      min: 0,
      max: CHART_WINDOW_SEC,
      grid: { color: 'rgba(90,90,90,0.35)', drawBorder: false, lineWidth: 1 },
      border: { display: true, color: '#444444' },
      ticks: {
        color: '#777777',
        font: { family: "'Share Tech Mono',monospace", size: 9 },
        maxTicksLimit: 11,
        stepSize: 1,
        callback: value => Number(value).toFixed(0)
      },
      title: {
        display: true,
        text: 'Time (s)',
        color: '#888888',
        font: { family: "'Share Tech Mono',monospace", size: 10 }
      }
    },
    y: {
      display: true,
      beginAtZero: false,
      grid: { color: 'rgba(90,90,90,0.35)', drawBorder: false, lineWidth: 1 },
      border: { display: true, color: '#444444' },
      ticks: { color: '#777777', font: { family: "'Share Tech Mono',monospace", size: 9 }, maxTicksLimit: 6 }
    }
  }
};

function chartOptions(chartId) {
  const yLabel = chartYLabels[chartId] || 'Value';
  return {
    ...chartDefaults,
    layout: { ...chartDefaults.layout, padding: { ...chartDefaults.layout.padding } },
    interaction: { ...chartDefaults.interaction },
    elements: { ...chartDefaults.elements, line: { ...chartDefaults.elements.line }, point: { ...chartDefaults.elements.point } },
    plugins: {
      legend: { ...chartDefaults.plugins.legend },
      tooltip: { ...chartDefaults.plugins.tooltip }
    },
    scales: {
      x: {
        ...chartDefaults.scales.x,
        grid: { ...chartDefaults.scales.x.grid },
        border: { ...chartDefaults.scales.x.border },
        ticks: { ...chartDefaults.scales.x.ticks, font: { ...chartDefaults.scales.x.ticks.font } },
        title: { ...chartDefaults.scales.x.title, font: { ...chartDefaults.scales.x.title.font } }
      },
      y: {
        ...chartDefaults.scales.y,
        grid: { ...chartDefaults.scales.y.grid },
        border: { ...chartDefaults.scales.y.border },
        ticks: { ...chartDefaults.scales.y.ticks, font: { ...chartDefaults.scales.y.ticks.font } },
        title: {
          display: true,
          text: yLabel,
          color: '#888888',
          font: { family: "'Share Tech Mono',monospace", size: 10 }
        }
      }
    }
  };
}

function mkChart(id, colors) {
  const canvas = document.getElementById(id);
  if (typeof Chart === 'undefined' || !canvas) {
    return { canvas: null, data: { datasets: colors.map(() => ({ data: [] })) }, options: { scales: { x: {}, y: {} } }, update() { } };
  }
  const ctx = canvas.getContext('2d');
  return new Chart(ctx, {
    type: 'line',
    data: {
      datasets: colors.map((c) => ({
        data: [],
        borderColor: c,
        backgroundColor: 'transparent',
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 3,
        pointBackgroundColor: c,
        pointBorderColor: c,
        tension: 0,
        fill: false,
        spanGaps: false
      }))
    },
    options: chartOptions(id),
    plugins: [darkChartBgPlugin]
  });
}

const charts = {
  cmpTemp: mkChart('cmpTemp', CHART_COLORS),
  cmpAlt: mkChart('cmpAlt', CHART_COLORS),
  cmpPres: mkChart('cmpPres', CHART_COLORS),
  cmpVel: mkChart('cmpVel', CHART_COLORS),
};

function updateChartYScale(chart) {
  if (!chart.options || !chart.options.scales || !chart.options.scales.y) return;
  const values = chart.data.datasets.flatMap(d => d.data.map(p => Number(p.y)).filter(Number.isFinite));
  if (values.length < 2) return;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, Math.abs(max) * 0.05, 1);
  const pad = span * 0.18;
  const allowNeg = chart.canvas && chart.canvas.id === 'cmpVel';
  chart.options.scales.y.min = allowNeg ? (min - pad) : Math.max(0, min - pad);
  chart.options.scales.y.max = max + pad;
}

// ============ WAVEFORM ============
function clearWave(id) {
  const el = document.getElementById(id); if (!el) return;
  el.setAttribute('data-pts', '');
  el.setAttribute('points', '');
  if (el.parentElement) el.parentElement.setAttribute('viewBox', '0 0 100 18');
}

function updateWave(id, val, max = 100) {
  if (!isConnected) { clearWave(id); return; }
  const el = document.getElementById(id); if (!el) return;
  const w = el.parentElement ? (el.parentElement.clientWidth || 100) : 100, h = 18;
  const jitter = (Math.random() - 0.5) * 2.5;
  let pts = el.getAttribute('data-pts') || '';
  const arr = pts ? pts.split(' ').map(p => { const [x, y] = p.split(','); return [parseFloat(x), parseFloat(y)]; }) : [];
  const x = arr.length ? arr[arr.length - 1][0] + w / 30 : 0, y = h / 2 - (val / max) * (h / 2 - 2) + jitter;
  arr.push([x, y]); if (arr.length > 30) arr.shift();
  const ox = arr[0][0], normalized = arr.map(([px, py]) => [px - ox, py]);
  el.setAttribute('data-pts', arr.map(p => p.join(',')).join(' '));
  el.setAttribute('points', normalized.map(p => p.join(',')).join(' '));
  if (el.parentElement) el.parentElement.setAttribute('viewBox', `0 0 ${normalized[normalized.length - 1][0] + 5} ${h}`);
}

// ============ CUBE ============
function updateCube(id, pitch, roll, yaw) {
  const el = document.getElementById(id);
  if (el) el.style.transform = `rotateX(${pitch}deg) rotateY(${yaw}deg) rotateZ(${roll}deg)`;
}

// ============ REAL SERIAL TELEMETRY ============
const serialState = {
  active: true,
  csvLogging: false,
  csvRows: [],
  availablePorts: [],
  rkt: { port: null, reader: null, keepReading: false, buffer: '', lastLineAt: 0, customBaud: null },
  cs: { port: null, reader: null, keepReading: false, buffer: '', lastLineAt: 0, customBaud: null },
};
const DEFAULT_BAUD = Number(new URLSearchParams(location.search).get('baud')) || 9600;

function roleConfig(role) {
  return role === 'cs'
    ? { selectId: 'cansatPortSelect', baudId: 'cansatBaudSelect', label: 'Cansat' }
    : { selectId: 'rocketPortSelect', baudId: 'rocketBaudSelect', label: 'Rocket' };
}

function selectedBaud(role) {
  const baudEl = document.getElementById(roleConfig(role).baudId);
  if (baudEl && baudEl.value === 'custom') return serialState[role].customBaud || DEFAULT_BAUD;
  return Number(baudEl && baudEl.value) || DEFAULT_BAUD;
}

function syncDefaultBaud() {
  ['rkt', 'cs'].forEach(role => {
    const baudEl = document.getElementById(roleConfig(role).baudId);
    if (!baudEl) return;
    const wanted = String(DEFAULT_BAUD);
    if ([...baudEl.options].some(opt => opt.value === wanted || opt.textContent === wanted)) {
      baudEl.value = wanted;
    } else {
      serialState[role].customBaud = DEFAULT_BAUD;
      baudEl.value = 'custom';
    }
  });
}

async function handleBaudChange(role) {
  const baudEl = document.getElementById(roleConfig(role).baudId);
  if (!baudEl) return;
  if (baudEl.value === 'custom') {
    const previous = serialState[role].customBaud || DEFAULT_BAUD;
    const entered = prompt('Enter baud rate', String(previous));
    const baud = Number(entered);
    if (!Number.isInteger(baud) || baud <= 0) {
      baudEl.value = [...baudEl.options].some(opt => opt.value === String(previous) || opt.textContent === String(previous)) ? String(previous) : 'custom';
      appendRawSerialLog('Invalid baud rate ignored', role);
      return;
    }
    serialState[role].customBaud = baud;
  }
  const link = serialState[role];
  if (link.port) {
    appendRawSerialLog(`Baud changed to ${selectedBaud(role)} - reconnecting port`, role);
    await connectSerial(role);
  }
}

function bindBaudControls() {
  ['rkt', 'cs'].forEach(role => {
    const baudEl = document.getElementById(roleConfig(role).baudId);
    if (baudEl) baudEl.addEventListener('change', () => handleBaudChange(role));
  });
}

function renderPortSelectors() {
  ['rkt', 'cs'].forEach(role => {
    const cfg = roleConfig(role);
    const select = document.getElementById(cfg.selectId);
    if (!select) return;

    const current = select.value;
    select.innerHTML = '<option value="request">Choose Port...</option>';

    serialState.availablePorts.forEach((port) => {
      const option = document.createElement("option");
      option.value = port.device;
      option.textContent = `${port.device} - ${port.description}`;
      select.appendChild(option);
    });

    if (current && [...select.options].some(opt => opt.value === current)) {
      select.value = current;
    }
  });
}

async function refreshGrantedPorts() {
  try {
    const response = await fetch(getApiUrl("/api/ports"));
    const result = await response.json();
    if (!result.success) throw new Error(result.error);
    serialState.availablePorts = result.ports;
    renderPortSelectors();
  } catch (err) {
    console.error("Unable to load COM ports:", err);
  }
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function appendRawSerialLog(line, role = 'RX') {
  const log = document.getElementById('rawSerialLog');
  if (!log) return;
  const stamp = new Date().toLocaleTimeString('en-GB', { hour12: false });
  const entry = `[${stamp}] ${role.toUpperCase()} ${line}`;
  if (log.querySelector('.muted')) log.textContent = '';
  log.textContent += `${entry}\n`;
  if (log.textContent.length > 40000) log.textContent = log.textContent.slice(-30000);
  log.scrollTop = log.scrollHeight;
  if (serialState.csvLogging) {
    serialState.csvRows.push([new Date().toISOString(), role.toUpperCase(), line]);
    updateCsvStateLabel();
  }
}

function clearRawSerialLog() {
  const log = document.getElementById('rawSerialLog');
  if (log) log.innerHTML = '<span class="muted">Waiting for data...</span>';
}

function updateCsvStateLabel() {
  const el = document.getElementById('csvState');
  if (!el) return;
  if (serialState.csvLogging) {
    el.textContent = `CSV Logging: ON \u2014 ${serialState.csvRows.length} records`;
    el.classList.add('active');
  } else {
    el.textContent = 'CSV Logging: OFF';
    el.classList.remove('active');
  }
}

async function startCsvLog() {
  serialState.csvLogging = true;
  serialState.csvRows = [];
  const startBtn = document.getElementById('startCsvLogBtn');
  const stopBtn = document.getElementById('stopCsvLogBtn');
  if (startBtn) startBtn.classList.add('idle-disabled');
  if (stopBtn) stopBtn.classList.remove('idle-disabled');
  updateCsvStateLabel();
  appendRawSerialLog('CSV logging started', 'LOG');

  try {
    const res = await fetch(getApiUrl("/api/logging/start"), { method: "POST" });
    const data = await res.json();
    if (data.success) {
      appendRawSerialLog('Backend CSV logger active', 'LOG');
    } else {
      appendRawSerialLog('Backend CSV logger start error: ' + data.error, 'LOG');
    }
  } catch (err) {
    appendRawSerialLog('Backend CSV start failed: ' + err.message, 'LOG');
  }
}

async function stopCsvLog() {
  serialState.csvLogging = false;
  const startBtn = document.getElementById('startCsvLogBtn');
  const stopBtn = document.getElementById('stopCsvLogBtn');
  if (startBtn) startBtn.classList.remove('idle-disabled');
  if (stopBtn) stopBtn.classList.add('idle-disabled');
  updateCsvStateLabel();

  try {
    await fetch(getApiUrl("/api/logging/stop"), { method: "POST" });
    appendRawSerialLog('Backend CSV logger stopped', 'LOG');
  } catch (err) {
    appendRawSerialLog('Backend CSV stop error: ' + err.message, 'LOG');
  }

  if (!serialState.csvRows.length) {
    appendRawSerialLog('CSV logging stopped - no raw rows captured', 'LOG');
    return;
  }
  const csv = ['timestamp,source,line', ...serialState.csvRows.map(row => row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(','))].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `telemetry-${new Date().toISOString().replace(/[:.]/g, '-')}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
  setText('csvState', `CSV Saved: ${serialState.csvRows.length} records`);
  appendRawSerialLog(`CSV logging stopped - ${serialState.csvRows.length} rows saved`, 'LOG');
}

function setCommsStatus() {
  const connected = !!(serialState.rkt.port || serialState.cs.port);
  const now = Date.now();
  const receiving = [serialState.rkt.lastLineAt, serialState.cs.lastLineAt].some(t => t && now - t < 3000);
  const connEl = document.getElementById('connectStatus');
  const recvEl = document.getElementById('receiveStatus');
  const connLed = document.getElementById('connectLed');
  const recvLed = document.getElementById('receiveLed');

  if (connEl) connEl.textContent = connected ? 'CONNECTED' : 'DISCONNECTED';
  if (recvEl) recvEl.textContent = receiving && serialState.active ? 'RECEIVING' : 'WAITING';
  if (connLed) connLed.className = `led ${connected ? 'green' : 'red'}`;
  if (recvLed) recvLed.className = `led ${receiving && serialState.active ? 'cyan' : 'orange'}`;
}

function gpsHealth(data) {
  if (!data.gpsValid) return { level: 'bad', text: 'NO GPS' };
  const fix = String(data.fix || '').toUpperCase();
  const sats = Number(data.sats) || 0;
  const hdop = Number(data.hdop) || Infinity;
  const hasSolidFix = fix.includes('3D') || fix.includes('DGPS') || fix.includes('RTK');
  const hasAnyFix = hasSolidFix || fix.includes('2D') || fix === '1';
  if (hasSolidFix && sats >= 6 && hdop > 0 && hdop < 2) return { level: 'good', text: 'GPS OK' };
  if (hasAnyFix || sats >= 4) return { level: 'warn', text: 'GPS WEAK' };
  return { level: 'bad', text: 'NO FIX' };
}

function applyGpsHealth(prefix, health) {
  const ids = [`${prefix}-gnss-time`, `${prefix}-lat`, `${prefix}-lon`, `${prefix}-gnss-alt`, `${prefix}-hdop`, `${prefix}-fix`];
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('gps-good', 'gps-warn', 'gps-bad');
    el.classList.add(`gps-${health.level}`);
  });
  const badge = document.getElementById(`${prefix}-sats`);
  if (badge) {
    badge.classList.remove('good', 'warn', 'bad');
    badge.classList.add(health.level);
    badge.title = health.text;
  }
}

function renderGpsTelemetry(prefix, data) {
  const health = gpsHealth(data);
  const timeStr = (data.gpsTime && data.gpsTime !== 'N/A') ? formatGpsTime(data.gpsTime) : 'N/A';
  setText(`${prefix}-gnss-time`, timeStr);

  if (!data.gpsValid) {
    setText(`${prefix}-lat`, '--');
    setText(`${prefix}-lon`, '--');
    setText(`${prefix}-gnss-alt`, '--');
    setText(`${prefix}-hdop`, '--');
    setText(`${prefix}-fix`, '--');
    setText(`${prefix}-sats`, '-- SATS');
    applyGpsHealth(prefix, health);
    return;
  }
  setText(`${prefix}-lat`, Number.isFinite(data.lat) && data.lat !== 0 ? `${data.lat.toFixed(5)} deg` : '--');
  setText(`${prefix}-lon`, Number.isFinite(data.lon) && data.lon !== 0 ? `${data.lon.toFixed(5)} deg` : '--');
  setText(`${prefix}-gnss-alt`, Number.isFinite(data.gpsAlt) ? `${data.gpsAlt.toFixed(0)} m` : '--');
  setText(`${prefix}-hdop`, Number.isFinite(data.hdop) && data.hdop > 0 ? data.hdop.toFixed(1) : '--');
  setText(`${prefix}-fix`, data.fix && data.fix !== 'N/A' ? data.fix : '--');
  setText(`${prefix}-sats`, Number.isFinite(data.sats) && data.sats > 0 ? `${data.sats} SATS` : '-- SATS');
  applyGpsHealth(prefix, health);
}

function updateTelemetryDisplay(advanceTime = true, pushCharts = true) {
  const rkt = state.rkt, cs = state.cs;
  if (gauges.rktAlt) gauges.rktAlt.update(rkt.alt, 1500);
  if (gauges.rktPres) gauges.rktPres.update(rkt.pres, 1013);
  if (gauges.rktTemp) gauges.rktTemp.update(rkt.temp, 60);
  if (gauges.rktVel) gauges.rktVel.update(rkt.vel, 400);
  if (gauges.csAlt) gauges.csAlt.update(cs.alt, 1000);
  if (gauges.csPres) gauges.csPres.update(cs.pres, 1013);
  if (gauges.csTemp) gauges.csTemp.update(cs.temp, 60);
  if (gauges.csVel) gauges.csVel.update(cs.vel, 300);

  ['ax', 'ay', 'az'].forEach(k => {
    setText(`rkt-${k}`, rkt[k].toFixed(2) + ' g');
    setText(`cs-${k}`, cs[k].toFixed(2) + ' g');
  });
  ['gx', 'gy', 'gz'].forEach(k => {
    setText(`rkt-${k}`, rkt[k].toFixed(1) + ' deg/s');
    setText(`cs-${k}`, cs[k].toFixed(1) + ' deg/s');
  });
  refreshGnssClock();
  renderGpsTelemetry('rkt', rkt);
  renderGpsTelemetry('cs', cs);
  setText('rkt-pitch', rkt.pitch.toFixed(1) + ' deg'); setText('rkt-roll', rkt.roll.toFixed(1) + ' deg'); setText('rkt-yaw', rkt.yaw.toFixed(1) + ' deg');
  setText('cs-pitch', cs.pitch.toFixed(1) + ' deg'); setText('cs-roll', cs.roll.toFixed(1) + ' deg'); setText('cs-yaw', cs.yaw.toFixed(1) + ' deg');

  ['voltage', 'packetCount', 'timeStamping', 'state'].forEach(k => {
    let idSuffix = k;
    if (k === 'packetCount') idSuffix = 'packet-count';
    if (k === 'timeStamping') idSuffix = 'time-stamping';

    const csEl = document.getElementById(`cs-${idSuffix}`);
    const rktEl = document.getElementById(`rkt-${idSuffix}`);

    if (csEl) {
      if (k === 'voltage') csEl.textContent = Number(cs.voltage).toFixed(2) + ' V';
      else if (k === 'timeStamping') csEl.textContent = cs.timeStamping + ' s';
      else csEl.textContent = cs[k];
    }
    if (rktEl) {
      if (k === 'voltage') rktEl.textContent = Number(rkt.voltage).toFixed(2) + ' V';
      else if (k === 'timeStamping') rktEl.textContent = rkt.timeStamping + ' s';
      else rktEl.textContent = rkt[k];
    }
  });

  updateCube('rkt-orientation-model', rkt.pitch, rkt.roll, rkt.yaw); updateCube('cs-orientation-model', cs.pitch, cs.roll, cs.yaw);
  updateWave('rkt-accel-wave', Math.abs(rkt.ax) * 10, 30); updateWave('cs-accel-wave', Math.abs(cs.ax) * 10, 30);
  updateWave('rkt-gyro-wave', Math.abs(rkt.gx) * 20, 40); updateWave('cs-gyro-wave', Math.abs(cs.gx) * 20, 40);

  if (advanceTime) state.t += 0.05;
  const lbl = state.t.toFixed(1);
  if (pushCharts) {
    pushChart(charts.cmpTemp, lbl, rkt.temp, cs.temp); pushChart(charts.cmpAlt, lbl, rkt.alt, cs.alt);
    pushChart(charts.cmpPres, lbl, rkt.pres, cs.pres); pushChart(charts.cmpVel, lbl, rkt.vel, cs.vel);
  }
  updateMap();
  setCommsStatus();
}

function pushChart(chart, lbl, ...vals) {
  if (!chart.data || !chart.data.datasets || !chart.data.datasets.length) return;
  const xValue = Math.max(0, Number(lbl) || 0);
  const chartId = chart && chart.canvas ? chart.canvas.id : '';
  const noise = simInterval ? (chartNoiseProfile[chartId] || [0, 0]) : [0, 0];   // fake jitter only in simulator

  vals.forEach((v, i) => {
    const value = Number(v);
    const noisy = sensorNoise(value, noise[i] || 0);
    chart.data.datasets[i].data.push({ x: xValue, y: chartId === 'cmpVel' ? noisy : Math.max(0, noisy) });
  });

  if (chart.data.datasets[0].data.length > CHART_MAX_POINTS) {
    chart.data.datasets.forEach(d => d.data.shift());
  }

  if (chart.options && chart.options.scales && chart.options.scales.x) {
    chart.options.scales.x.min = Math.max(0, xValue - CHART_WINDOW_SEC);
    chart.options.scales.x.max = Math.max(CHART_WINDOW_SEC, xValue);
  }

  updateChartYScale(chart);
  updateChartReadout(chart, vals[0]);
  chart.update('none');
}

function updateChartReadout(chart, value) {
  const readoutMap = {
    cmpTemp: 'cmpTempReadout',
    cmpAlt: 'cmpAltReadout',
    cmpPres: 'cmpPresReadout',
    cmpVel: 'cmpVelReadout'
  };
  const chartId = chart && chart.canvas ? chart.canvas.id : '';
  const el = document.getElementById(readoutMap[chartId]);
  const num = Number(value);
  if (el && Number.isFinite(num)) el.textContent = chartId === 'cmpPres' ? num.toFixed(0) : num.toFixed(1);
}

function normalizeTelemetryKey(key) {
  return String(key)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '');
}

function parseTelemetryLine(line, fallbackRole) {
  const clean = line.trim();
  if (!clean) return null;
  if (clean.startsWith('{')) {
    try {
      const data = JSON.parse(clean);
      const roleText = (data.role || data.type || fallbackRole || '').toString().toLowerCase();
      const role = roleText.includes('cs') || roleText.includes('can') ? 'cs' : 'rkt';
      return { role, values: data, isGps: Object.keys(data).some(key => ['lat', 'latitude', 'gpslat', 'lon', 'long', 'lng', 'longitude', 'gpslon', 'gpslng', 'gpstime', 'time', 'utctime', 'hdop', 'fix', 'gpsfix', 'sats', 'satellites'].includes(normalizeTelemetryKey(key))) };
    } catch (err) { return null; }
  }

  const withoutChecksum = clean.split('*')[0];
  const parts = withoutChecksum.split(',').map(p => p.trim());
  const header = (parts[0] || '').toUpperCase();

  if (parts.length >= 20) {
    const role = (header.includes('888819') || header.includes('VYOMNAUTS') || header.includes('-CS') || header.includes('CANSAT') || fallbackRole === 'cs') ? 'cs' : 'rkt';
    const stateMap = { 0: 'LAUNCH_PAD', 1: 'ASCENT', 2: 'APOGEE', 3: 'DESCENT', 4: 'MAIN_DEPLOYED', 5: 'IMPACT' };
    const rawState = parseInt(parts[18]);
    const stateStr = !isNaN(rawState) && stateMap[rawState] !== undefined ? stateMap[rawState] : (parts[18] || 'LAUNCH_PAD');

    const values = {};
    values.teamId = parts[0];
    values.timeStamping = parts[1];
    values.packetCount = parts[2];
    values.alt = parts[3];
    values.pres = parts[4];
    values.temp = parts[5];
    values.voltage = parts[6];
    values.gpsTime = parts[7];
    values.lat = parts[8];
    values.lon = parts[9];
    values.gpsAlt = parts[10];
    values.sats = parts[11];
    values.ax = parts[12];
    values.ay = parts[13];
    values.vel = parts[14];
    values.gx = parts[15];
    values.gy = parts[16];
    values.gz = parts[17];
    values.state = stateStr;
    values.ch4 = parts[19];
    values.co = parts[20];
    values.nh3 = parts[21];
    values.co2 = parts[22];
    if (parts[23] !== undefined) values.pitch = parts[23];
    if (parts[24] !== undefined) values.roll = parts[24];
    if (parts[25] !== undefined) values.yaw = parts[25];

    return { role, values, isGps: true };
  }

  if (parts.length >= 14 && (header.startsWith('2026') || header.includes('IN-SPACE') || header.includes('INSPAC') || header.includes('888819'))) {
    const role = (header.includes('-CS') || header.includes('CANSAT') || header.includes('888819') || fallbackRole === 'cs') ? 'cs' : 'rkt';
    const values = {};

    values.teamId = parts[0];
    values.timeStamping = parts[1];
    values.packetCount = parts[2];
    values.alt = parts[3];

    const rawPres = parseFloat(parts[4]);
    if (!isNaN(rawPres)) {
      values.pres = rawPres > 5000 ? (rawPres / 100).toString() : rawPres.toString();
    }

    values.temp = parts[5];
    values.voltage = parts[6];
    values.gpsTime = parts[7];
    values.lat = parts[8];
    values.lon = parts[9];
    values.gpsAlt = parts[10];
    values.sats = parts[11];

    const accField = parts[12] || '';
    if (accField.includes('/') || accField.includes(';')) {
      const accParts = accField.split(/[\/;]/).map(Number);
      if (accParts.length >= 3) {
        values.ax = accParts[0].toString();
        values.ay = accParts[1].toString();
        values.az = accParts[2].toString();
      } else {
        values.az = accParts[0].toString();
      }
    } else {
      values.az = accField;
    }

    const gyroField = parts[13] || '';
    if (gyroField.includes('/') || gyroField.includes(';')) {
      const gyroParts = gyroField.split(/[\/;]/).map(Number);
      if (gyroParts.length >= 3) {
        values.gx = gyroParts[0].toString();
        values.gy = gyroParts[1].toString();
        values.gz = gyroParts[2].toString();
      } else {
        values.gx = gyroParts[0].toString();
      }
    } else {
      values.gx = gyroField;
    }

    values.state = parts[14];

    if (parts[15]) {
      const optParts = parts[15].split(/[;,]/).map(p => p.trim());
      optParts.forEach(opt => {
        const [k, v] = opt.split(':').map(x => x.trim());
        if (k && v) {
          values[normalizeTelemetryKey(k)] = v;
        }
      });
    }

    return { role, values, isGps: true };
  }

  const role = header.includes('CSDATA') || header.includes('CANSAT') || fallbackRole === 'cs' ? 'cs' : 'rkt';
  const values = {};
  const numericFields = header.includes('GNSS')
    ? ['gpsTime', 'lat', 'lon', 'alt', 'sats', 'hdop', 'fix']
    : ['alt', 'pres', 'temp', 'vel', 'ax', 'ay', 'az', 'gx', 'gy', 'gz', 'pitch', 'roll', 'yaw', 'lat', 'lon'];
  const dataStart = /^[A-Z_$]+[A-Z0-9_$-]*$/i.test(parts[0] || '') ? 1 : 0;

  parts.slice(dataStart).forEach((part, index) => {
    if (part.includes(':') || part.includes('=')) {
      const sep = part.includes(':') ? ':' : '=';
      const [rawKey, ...rest] = part.split(sep);
      const rawVal = rest.join(sep);
      values[normalizeTelemetryKey(rawKey)] = rawVal.trim();
    } else if (header.includes('GNSS')) {
      values[numericFields[index]] = part;
    } else if (!Number.isNaN(parseFloat(part))) {
      values[numericFields[index]] = part;
    }
  });
  return { role, values, isGps: header.includes('GNSS') };
}

function applyTelemetry(line, fallbackRole) {
  if (!line.trim()) return;
  appendRawSerialLog(line, fallbackRole || 'rx');
  const parsed = parseTelemetryLine(line, fallbackRole);
  if (!parsed) {
    setText('dataStream', line);
    return;
  }
  const target = state[parsed.role];
  const map = {
    altitude: 'alt', altitudem: 'alt', height: 'alt', heightm: 'alt',
    pressure: 'pres', pre: 'pres', press: 'pres', preshpa: 'pres', pressurehpa: 'pres', baro: 'pres', baropressure: 'pres',
    tmp: 'temp', temperature: 'temp', tempc: 'temp', temperaturec: 'temp',
    velocity: 'vel', velocityms: 'vel', speed: 'vel', speedms: 'vel',
    accx: 'ax', accelx: 'ax', accelerationx: 'ax',
    accy: 'ay', accely: 'ay', accelerationy: 'ay',
    accz: 'az', accelz: 'az', accelerationz: 'az',
    gyrox: 'gx', gyroscopex: 'gx',
    gyroy: 'gy', gyroscopey: 'gy',
    gyroz: 'gz', gyroscopez: 'gz',
    pitchdeg: 'pitch', rolldeg: 'roll', yawdeg: 'yaw',
    latitude: 'lat', gpslat: 'lat',
    longitude: 'lon', long: 'lon', lng: 'lon', gpslon: 'lon', gpslong: 'lon', gpslng: 'lon',
    gpsalt: 'gpsAlt', gpsaltitude: 'gpsAlt',
    gpstime: 'gpsTime', time: 'gpsTime', utctime: 'gpsTime',
    hdop: 'hdop', gpshdop: 'hdop',
    fix: 'fix', gpsfix: 'fix', fixquality: 'fix',
    satellite: 'sats', satellites: 'sats', sat: 'sats', sats: 'sats', numsats: 'sats',
    voltage: 'voltage', volt: 'voltage',
    state: 'state', swstate: 'state', softwarestate: 'state',
    packetcount: 'packetCount', pckt: 'packetCount', packet: 'packetCount',
    timestamping: 'timeStamping', uptime: 'timeStamping'
  };
  let receivedGpsField = !!parsed.isGps;
  Object.entries(parsed.values).forEach(([key, val]) => {
    const normalizedKey = normalizeTelemetryKey(key);
    const field = map[normalizedKey] || normalizedKey;
    if (!(field in target)) return;
    if (['gpsTime', 'lat', 'lon', 'hdop', 'fix', 'sats'].includes(field)) receivedGpsField = true;
    if (['gpsTime', 'fix'].includes(field)) {
      target[field] = String(val).trim() || 'N/A';
      return;
    }
    if (field === 'timeStamping') {
      const num = Number(val);
      target[field] = num > 100000 ? Math.floor(num / 1000) : num;
      return;
    }
    if (Number.isFinite(Number(val))) target[field] = Number(val);
  });
  if (receivedGpsField) target.gpsValid = true;
  setText('dataStream', line);
  serialState[parsed.role].lastLineAt = Date.now();
  if (serialState.active) updateTelemetryDisplay();
}

async function connectSerial(role) {
  const link = serialState[role];
  const cfg = roleConfig(role);
  const selectEl = document.getElementById(cfg.selectId);
  const selectedPort = selectEl && selectEl.value ? selectEl.value : null;

  // Prevent auto-defaulting to COM5/COM6 if no physical port selection has been made
  if (!selectedPort || selectedPort === 'request') {
    appendRawSerialLog(`No port selected for ${cfg.label}. Please select a physical COM port.`, role);
    setCommsStatus();
    return;
  }

  const baudRate = selectedBaud(role);
  try {
    const endpoint = getApiUrl(
      role === "rkt"
        ? "/api/connect/rocket"
        : "/api/connect/cansat"
    );

    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        port: selectedPort,
        baudrate: baudRate
      })
    });

    const result = await response.json();

    if (!result.success) {
      throw new Error(result.message || result.error);
    }

    serialState[role].port = selectedPort;
    serialState.active = true;

    appendRawSerialLog(
      `${cfg.label} connected via backend on ${selectedPort} (${baudRate} baud)`,
      role
    );

    setCommsStatus();
  } catch (err) {
    appendRawSerialLog(`Connection failed: ${err.message}`, role);
    setCommsStatus();
  }
}

async function connectSelectedSerials() {
  const rocketEl = document.getElementById('rocketPortSelect');
  const cansatEl = document.getElementById('cansatPortSelect');
  const rocketSelected = rocketEl ? rocketEl.value : '';
  const cansatSelected = cansatEl ? cansatEl.value : '';
  if ((!rocketSelected || rocketSelected === 'request') && (!cansatSelected || cansatSelected === 'request')) {
    appendRawSerialLog('Please select a COM port from the dropdown before connecting.', 'LOG');
    return;
  }
  if (rocketSelected && rocketSelected !== 'request') await connectSerial('rkt');
  if (cansatSelected && cansatSelected !== 'request') await connectSerial('cs');
}

async function readSerialLoop(role) {
  const link = serialState[role];
  const decoder = new TextDecoder();
  while (link.port && link.keepReading) {
    try {
      link.reader = link.port.readable.getReader();
      while (link.keepReading) {
        const { value, done } = await link.reader.read();
        if (done) {
          link.keepReading = false;
          break;
        }
        link.buffer += decoder.decode(value, { stream: true });
        const lines = link.buffer.split(/\r\n|\n|\r/);
        link.buffer = lines.pop() || '';
        lines.forEach(line => applyTelemetry(line, role));
      }
    } catch (err) {
      setText('dataStream', `Serial ${role.toUpperCase()} read error: ${err.message}`);
      appendRawSerialLog(`Read error: ${err.message}`, role);
      link.keepReading = false;
    } finally {
      if (link.reader) {
        try { link.reader.releaseLock(); } catch (err) { }
        link.reader = null;
      }
    }
  }
  if (link.port && !link.keepReading) {
    try { await link.port.close(); } catch (err) { }
    link.port = null;
    setCommsStatus();
  }
}

async function startTelemetry() {
  let succeeded = 0;

  try {
    const response = await fetch(getApiUrl("/api/command/rocket"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: "START_TELEMETRY" })
    });
    const result = await response.json();
    if (result.success) {
      succeeded++;
      appendRawSerialLog("ROCKET TX: START_TELEMETRY (SENT)", "RKT");
    } else {
      appendRawSerialLog("ROCKET TX FAILED: " + (result.error || "Device not connected"), "RKT");
    }
  } catch (err) {
    appendRawSerialLog("ROCKET TX ERROR: " + err.message, "RKT");
  }

  try {
    const response = await fetch(getApiUrl("/api/command/cansat"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: "START_TELEMETRY" })
    });
    const result = await response.json();
    if (result.success) {
      succeeded++;
      appendRawSerialLog("CANSAT TX: START_TELEMETRY (SENT)", "CS");
    } else if (serialState.cs.port) {
      appendRawSerialLog("CANSAT TX FAILED: " + (result.error || "Device not connected"), "CS");
    }
  } catch (err) { }

  if (succeeded > 0) {
    serialState.active = true;
    setCommsStatus();
  }
}

function stopTelemetry() {
  serialState.active = false;
  setCommsStatus();
}

async function closeSerialLink(role) {
  const link = serialState[role];
  link.keepReading = false;
  if (link.reader) {
    try { await link.reader.cancel(); } catch (err) { }
    try { link.reader.releaseLock(); } catch (err) { }
    link.reader = null;
  }
  if (link.port) {
    try { await link.port.close(); } catch (err) { }
    link.port = null;
  }
}

async function disconnectSerial() {
  try {
    await fetch(getApiUrl("/api/disconnect/rocket"), { method: "POST" });
    await fetch(getApiUrl("/api/disconnect/cansat"), { method: "POST" });

    const rktSel = document.getElementById("rocketPortSelect");
    const csSel = document.getElementById("cansatPortSelect");
    if (rktSel) rktSel.value = "request";
    if (csSel) csSel.value = "request";

    serialState.rkt.port = null;
    serialState.cs.port = null;

    isConnected = false;
    serialState.active = false;

    applyIdleState();

    appendRawSerialLog("Rocket disconnected", "rkt");
    appendRawSerialLog("CanSat disconnected", "cs");
    setCommsStatus();
  } catch (err) {
    appendRawSerialLog(`Disconnect failed: ${err.message}`, "LOG");
  }
}

// ============ SIMULATE ============
const streamLines = [
  () => {
    state.rkt.packetCount++;
    const rkt = state.rkt;
    const timeStamping = Math.floor(state.t);
    state.rkt.timeStamping = timeStamping;
    state.rkt.voltage = 5.04 + (Math.random() - 0.5) * 0.05;
    state.rkt.state = timeStamping < 10 ? 'BOOT' : (timeStamping < 30 ? 'LAUNCH_DETECT' : 'APOGEE_DETECT');
    return `2026 IN-SPACe-045,${timeStamping},${rkt.packetCount},${rkt.alt.toFixed(1)},${(rkt.pres * 100).toFixed(0)},${rkt.temp.toFixed(1)},${rkt.voltage.toFixed(2)},${currentUtcGpsCompact()},${rkt.lat.toFixed(5)},${rkt.lon.toFixed(5)},${rkt.alt.toFixed(1)},${rkt.sats},${rkt.ax.toFixed(2)}/${rkt.ay.toFixed(2)}/${rkt.az.toFixed(2)},${rkt.gx.toFixed(1)}/${rkt.gy.toFixed(1)}/${rkt.gz.toFixed(1)},${state.rkt.state},PITCH:${rkt.pitch.toFixed(1)};ROLL:${rkt.roll.toFixed(1)};YAW:${rkt.yaw.toFixed(1)};VEL:${rkt.vel.toFixed(1)}`;
  },
  () => {
    state.cs.packetCount++;
    const cs = state.cs;
    const timeStamping = Math.floor(state.t);
    state.cs.timeStamping = timeStamping;
    state.cs.voltage = 4.92 + (Math.random() - 0.5) * 0.04;
    state.cs.state = timeStamping < 15 ? 'BOOT' : (timeStamping < 35 ? 'DESCENT' : 'DEPLOY');
    return `2026 IN-SPACe-045,${timeStamping},${cs.packetCount},${cs.alt.toFixed(1)},${(cs.pres * 100).toFixed(0)},${cs.temp.toFixed(1)},${cs.voltage.toFixed(2)},${currentUtcGpsCompact()},${cs.lat.toFixed(5)},${cs.lon.toFixed(5)},${cs.alt.toFixed(1)},${cs.sats},${cs.ax.toFixed(2)}/${cs.ay.toFixed(2)}/${cs.az.toFixed(2)},${cs.gx.toFixed(1)}/${cs.gy.toFixed(1)}/${cs.gz.toFixed(1)},${state.cs.state},PITCH:${cs.pitch.toFixed(1)};ROLL:${cs.roll.toFixed(1)};YAW:${cs.yaw.toFixed(1)};VEL:${cs.vel.toFixed(1)}`;
  }
];
let streamIdx = 0;

function simulate() {
  state.t += 0.05 * state.simSpeed;
  const t = state.t, rkt = state.rkt, cs = state.cs;

  rkt.alt = Math.max(0, 500 + 300 * Math.sin(t * 0.15) + 50 * Math.sin(t * 2.7) + 18 * Math.random());
  rkt.pres = 1013.25 * Math.pow(1 - rkt.alt * 2.2577e-5, 5.2559) + Math.sin(t * 3.1) * 1.2 + Math.random() * 0.8;
  rkt.temp = 15 - 0.0065 * rkt.alt + Math.sin(t * 1.4) * 2.5 + Math.cos(t * 0.7) * 1.1;
  rkt.vel = Math.max(0, 120 + 80 * Math.cos(t * 0.12) + 35 * Math.sin(t * 0.55) + 12 * (Math.random() - 0.5));
  rkt.ax = 0.2 + 0.8 * Math.sin(t * 1.3) + Math.random() * 0.1;
  rkt.ay = 0.1 + 0.4 * Math.cos(t * 0.9) + Math.random() * 0.1;
  rkt.az = 9.81 + Math.sin(t * 2) * 0.5;
  rkt.gx = 1.5 * Math.sin(t * 0.7) + Math.random() * 0.2;
  rkt.gy = 0.8 * Math.cos(t * 0.5) + Math.random() * 0.15;
  rkt.gz = 0.4 * Math.sin(t * 1.1) + Math.random() * 0.1;
  rkt.pitch = 15 * Math.sin(t * 0.2); rkt.roll = 10 * Math.cos(t * 0.15); rkt.yaw = (rkt.yaw + 0.3) % 360;
  rkt.lat += 0.00002 * (Math.random() - 0.5); rkt.lon += 0.00002 * (Math.random() - 0.5);
  rkt.sats = 8; rkt.hdop = 0.9; rkt.fix = '3D'; rkt.gpsAlt = rkt.alt;

  cs.alt = Math.max(0, 350 + 180 * Math.sin(t * 0.1 + 1) + 40 * Math.sin(t * 2.2) + 12 * Math.random());
  cs.pres = 1013.25 * Math.pow(1 - cs.alt * 2.2577e-5, 5.2559) + Math.sin(t * 2.8) * 0.9 + Math.random() * 0.6;
  cs.temp = 18 - 0.0065 * cs.alt + Math.cos(t * 1.2) * 2.1 + Math.sin(t * 0.9) * 0.9;
  cs.vel = Math.max(0, 80 + 50 * Math.cos(t * 0.1) + 28 * Math.sin(t * 0.48) + 10 * (Math.random() - 0.5));
  cs.ax = 0.1 + 0.5 * Math.cos(t * 1.1) + Math.random() * 0.08;
  cs.ay = 0.2 + 0.3 * Math.sin(t * 0.8) + Math.random() * 0.08;
  cs.az = 9.75 + Math.cos(t * 1.8) * 0.4;
  cs.gx = 0.9 * Math.cos(t * 0.6) + Math.random() * 0.15;
  cs.gy = 0.5 * Math.sin(t * 0.4) + Math.random() * 0.1;
  cs.gz = 0.3 * Math.cos(t * 0.9) + Math.random() * 0.08;
  cs.pitch = 8 * Math.cos(t * 0.18); cs.roll = 6 * Math.sin(t * 0.12); cs.yaw = (cs.yaw + 0.2) % 360;
  cs.lat += 0.000015 * (Math.random() - 0.5); cs.lon += 0.000015 * (Math.random() - 0.5);
  cs.sats = 6; cs.hdop = 1.2; cs.fix = '3D'; cs.gpsAlt = cs.alt;

  syncIndiaGpsTime();
  updateTelemetryDisplay(false);

  if (Math.random() < 0.3) {
    const line = streamLines[streamIdx % streamLines.length]();
    const role = (line.includes('$CSDATA') || line.includes('-CS')) ? 'cs' : 'rkt';
    setText('dataStream', line);
    serialState[role].lastLineAt = Date.now();
    appendRawSerialLog(line, role);
    setCommsStatus();
    streamIdx++;
  }
}

function toggleSim(btn) {
  if (btn.textContent === 'SIM ON') {
    btn.textContent = 'SIM OFF';
    btn.style.borderColor = 'var(--dim)';
    btn.style.color = 'var(--dim)';
    clearInterval(simInterval);
    simInterval = null;
    isConnected = false;
    if (!(serialState.rkt.port || serialState.cs.port)) serialState.active = false;
    appendRawSerialLog('Simulation stopped', 'LOG');
    setCommsStatus();
    applyIdleState();
  }
  else {
    btn.textContent = 'SIM ON';
    btn.style.borderColor = '';
    btn.style.color = '';
    serialState.active = true;
    isConnected = true;
    clearIdleState();
    state.t = 0;
    state.rkt.packetCount = 0;
    state.cs.packetCount = 0;
    appendRawSerialLog('Simulation started - dummy telemetry flowing', 'LOG');
    simulate();
    simInterval = setInterval(simulate, 50);
    setCommsStatus();
  }
}

function setSpeed(s, btn) {
  state.simSpeed = s;
  document.querySelectorAll('.speed-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  if (simInterval) {
    clearInterval(simInterval);
    simInterval = setInterval(simulate, 50 / s * 2);
  }
}

// ============ IDLE STATE MANAGEMENT ============
function applyIdleState() {
  chartT0 = null;
  lastChartPc.rkt = -1;
  lastChartPc.cs = -1;
  ['rktAlt', 'rktPres', 'rktTemp', 'rktVel', 'csAlt', 'csPres', 'csTemp', 'csVel'].forEach(id => {
    if (gauges[id] && gauges[id].setIdle) gauges[id].setIdle();
  });

  [
    'rkt-ax', 'rkt-ay', 'rkt-az', 'rkt-gx', 'rkt-gy', 'rkt-gz',
    'rkt-gnss-time', 'rkt-lat', 'rkt-lon', 'rkt-gnss-alt', 'rkt-hdop', 'rkt-fix',
    'rkt-pitch', 'rkt-roll', 'rkt-yaw',
    'rkt-voltage', 'rkt-packet-count', 'rkt-time-stamping', 'rkt-state',
    'cs-ax', 'cs-ay', 'cs-az', 'cs-gx', 'cs-gy', 'cs-gz',
    'cs-gnss-time', 'cs-lat', 'cs-lon', 'cs-gnss-alt', 'cs-hdop', 'cs-fix',
    'cs-pitch', 'cs-roll', 'cs-yaw',
    'cs-voltage', 'cs-packet-count', 'cs-time-stamping', 'cs-state'
  ].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = id.includes('gnss-time') ? 'N/A' : '--';
  });
  setText('rkt-sats', '-- SATS');
  setText('cs-sats', '-- SATS');

  ['cmpTempReadout', 'cmpAltReadout', 'cmpPresReadout', 'cmpVelReadout'].forEach(id => setText(id, '--'));
  ['rkt-accel-wave', 'rkt-gyro-wave', 'cs-accel-wave', 'cs-gyro-wave'].forEach(id => clearWave(id));

  // Only show the placeholder if the log is empty. Idle state is re-applied on every
  // 10 Hz snapshot until valid packets arrive; it must not wipe raw lines the user is
  // trying to read (that made the serial log flash and vanish).
  const log = document.getElementById('rawSerialLog');
  if (log && !log.textContent.trim()) log.innerHTML = '<span class="muted">No incoming data \u2014 port not connected.</span>';

  const startBtn = document.getElementById('startCsvLogBtn');
  const stopBtn = document.getElementById('stopCsvLogBtn');
  if (startBtn) startBtn.classList.remove('idle-disabled');
  if (stopBtn) stopBtn.classList.add('idle-disabled');
  if (serialState.csvLogging) {
    serialState.csvLogging = false;
    updateCsvStateLabel();
  } else {
    updateCsvStateLabel();
  }

  setText('connectStatus', 'DISCONNECTED');
  setText('receiveStatus', 'NO SIGNAL');
  const connLed = document.getElementById('connectLed');
  const recvLed = document.getElementById('receiveLed');
  if (connLed) connLed.className = 'led red';
  if (recvLed) recvLed.className = 'led gray';

  setText('dist-cs-rkt', '--');
  setText('dist-cs-gnd', '--');
  setText('dist-rkt-gnd', '--');
  setMapIdleOverlay(true);
  setLeafletMarkersVisible(false);
  setOfflineMarkersVisible(false);

  document.querySelectorAll('.status-pill').forEach(p => p.className = 'status-pill inactive');

  ['cmpTemp', 'cmpAlt', 'cmpPres', 'cmpVel'].forEach(id => {
    const c = charts[id];
    if (c && c.data) { c.data.datasets.forEach(d => d.data = []); c.update('none'); }
  });
}

async function fetchBackendTelemetry() {
  try {
    const response = await fetch(getApiUrl("/api/telemetry"));
    const data = await response.json();
    applySnapshotTelemetry(data);
  } catch (err) {
    console.error("fetchBackendTelemetry error:", err);
  }
}

// ============ ML - TERRAIN CLASSIFICATION ============

function handleTerrainFileSelected(input) {
  const file = input && input.files && input.files[0] ? input.files[0] : null;
  terrainSelectedFile = file;

  const nameEl = document.getElementById('terrainFileName');
  const uploadBtn = document.getElementById('terrainUploadBtn');

  if (nameEl) nameEl.textContent = file ? file.name : 'No file selected';

  if (uploadBtn) {
    if (file && !terrainUploading) {
      uploadBtn.classList.remove('idle-disabled');
    } else {
      uploadBtn.classList.add('idle-disabled');
    }
  }
}

async function uploadTerrainImage(input) {
  const file = (input && input.files && input.files[0]) ? input.files[0] : terrainSelectedFile;
  if (!file) {
    console.warn("uploadTerrainImage: No file selected.");
    return;
  }
  terrainSelectedFile = file;
  await uploadTerrainFrame();
}

async function uploadTerrainFrame() {
  if (!terrainSelectedFile || terrainUploading) return;

  const uploadBtn = document.getElementById('terrainUploadBtn');
  const nameEl = document.getElementById('terrainFileName');

  terrainUploading = true;
  if (uploadBtn) uploadBtn.classList.add('idle-disabled');
  if (nameEl) nameEl.textContent = 'Uploading...';

  try {
    const formData = new FormData();
    formData.append('image', terrainSelectedFile);

    const targetUrl = getApiUrl('/api/predict/terrain');
    let response;
    try {
      response = await fetch(targetUrl, {
        method: "POST",
        body: formData
      });
    } catch (fetchErr) {
      if (targetUrl !== 'http://localhost:5000/api/predict/terrain') {
        response = await fetch('http://localhost:5000/api/predict/terrain', {
          method: "POST",
          body: formData
        });
      } else {
        throw fetchErr;
      }
    }

    const data = await response.json();

    if (!response.ok || !data.success) {
      throw new Error(data.error || `Request failed (${response.status})`);
    }

    renderTerrainPanel({
      class_name: data.class_name,
      confidence: data.confidence,
      probabilities: data.probabilities,
      last_update: Date.now() / 1000,
      available: true
    });

    if (nameEl) nameEl.textContent = terrainSelectedFile.name;
  } catch (err) {
    console.error("uploadTerrainFrame error:", err);
    if (nameEl) nameEl.textContent = `Error: ${err.message}`;
  } finally {
    terrainUploading = false;
    if (uploadBtn && terrainSelectedFile) uploadBtn.classList.remove('idle-disabled');
  }
}

function renderTerrainPanel(terrain) {
  const classEl = document.getElementById('terrain-class-name');
  const confEl = document.getElementById('terrain-confidence');
  const tsEl = document.getElementById('terrain-updated');
  const listEl = document.getElementById('terrain-prob-list');
  const ledEl = document.getElementById('terrain-led');

  if (!terrain || !terrain.available) {
    if (classEl) classEl.textContent = '--';
    if (confEl) confEl.textContent = '-- %';
    if (tsEl) tsEl.textContent = 'No frame analyzed yet';
    if (ledEl) ledEl.className = 'led cyan';
    if (listEl) listEl.innerHTML = '<div class="no-data-placeholder">No terrain frame analyzed yet.</div>';
    return;
  }

  if (classEl) classEl.textContent = terrain.class_name || '--';
  if (confEl) confEl.textContent = (terrain.confidence !== undefined ? (typeof terrain.confidence === 'number' ? terrain.confidence.toFixed(1) : terrain.confidence) : '--') + ' %';
  if (ledEl) ledEl.className = 'led green';

  if (tsEl && terrain.last_update) {
    const d = new Date(terrain.last_update * 1000);
    tsEl.textContent = 'Updated ' + d.toLocaleTimeString();
  }

  if (listEl && terrain.probabilities) {
    const entries = Object.entries(terrain.probabilities)
      .sort((a, b) => b[1] - a[1]);

    listEl.innerHTML = entries.map(([name, pct]) => `
      <div class="terrain-prob-row${name === terrain.class_name ? ' top' : ''}">
        <span class="terrain-prob-label">${name}</span>
        <span class="terrain-prob-track"><span class="terrain-prob-fill" style="width:${Math.max(0, Math.min(100, pct))}%;"></span></span>
        <span class="terrain-prob-pct">${typeof pct === 'number' ? pct.toFixed(1) : pct}%</span>
      </div>
    `).join('');
  }
}

function applySnapshotTelemetry(data) {
  if (!data) return;

  const rktConnectedWithPackets = !!(data.rocket_connected && data.rocket && (data.rocket.packet_count > 0));
  const csConnectedWithPackets = !!(data.cansat_connected && data.cansat && (data.cansat.packet_count > 0));

  if (rktConnectedWithPackets || csConnectedWithPackets) {
    isConnected = true;
    serialState.active = true;
    clearIdleState();
  } else if (!simInterval) {
    isConnected = false;
    serialState.active = false;
    applyIdleState();
  }

  if (data.rocket) {
    state.rkt.alt = data.rocket.altitude || 0;
    state.rkt.temp = data.rocket.temperature || 0;
    state.rkt.pres = data.rocket.pressure || 0;
    state.rkt.voltage = data.rocket.voltage || 0;
    state.rkt.packetCount = data.rocket.packet_count || 0;
    state.rkt.state = data.rocket.flight_state || '--';
    if (data.rocket.timestamp !== undefined && data.rocket.timestamp !== null) {
      const ts = Number(data.rocket.timestamp);
      state.rkt.timeStamping = ts > 100000 ? Math.floor(ts / 1000) : ts;
    }
    if (data.rocket.gnss_time) {
      state.rkt.gpsTime = data.rocket.gnss_time;
    }
    if (data.rocket.vertical_velocity !== undefined) state.rkt.vel = data.rocket.vertical_velocity;

    if (data.rocket.accelerometer) {
      state.rkt.ax = data.rocket.accelerometer.x || 0;
      state.rkt.ay = data.rocket.accelerometer.y || 0;
      state.rkt.az = data.rocket.accelerometer.z || 0;
    }

    if (data.rocket.gyroscope) {
      state.rkt.gx = data.rocket.gyroscope.x || 0;
      state.rkt.gy = data.rocket.gyroscope.y || 0;
      state.rkt.gz = data.rocket.gyroscope.z || 0;
    }

    if (data.rocket.orientation) {
      state.rkt.pitch = data.rocket.orientation.pitch || 0;
      state.rkt.roll = data.rocket.orientation.roll || 0;
      state.rkt.yaw = data.rocket.orientation.yaw || 0;
    }

    if (data.rocket.gas) {
      state.rkt.ch4 = data.rocket.gas.ch4 || 0;
      state.rkt.co = data.rocket.gas.co || 0;
      state.rkt.nh3 = data.rocket.gas.nh3 || 0;
      state.rkt.co2 = data.rocket.gas.co2 || 0;
    }

    if (data.rocket.gnss_latitude || data.rocket.gnss_longitude || data.rocket.gnss_sats || data.rocket.gnss_time) {
      state.rkt.lat = data.rocket.gnss_latitude || 0;
      state.rkt.lon = data.rocket.gnss_longitude || 0;
      state.rkt.gpsAlt = data.rocket.gnss_altitude || 0;
      state.rkt.sats = data.rocket.gnss_sats || 0;
      state.rkt.gpsValid = true;
    }
  }

  if (data.cansat) {
    state.cs.alt = data.cansat.altitude || 0;
    state.cs.temp = data.cansat.temperature || 0;
    state.cs.pres = data.cansat.pressure || 0;
    state.cs.voltage = data.cansat.voltage || 0;
    state.cs.packetCount = data.cansat.packet_count || 0;
    state.cs.state = data.cansat.flight_state || '--';
    if (data.cansat.timestamp !== undefined && data.cansat.timestamp !== null) {
      const ts = Number(data.cansat.timestamp);
      state.cs.timeStamping = ts > 100000 ? Math.floor(ts / 1000) : ts;
    }
    if (data.cansat.gnss_time) {
      state.cs.gpsTime = data.cansat.gnss_time;
    }
    if (data.cansat.vertical_velocity !== undefined) state.cs.vel = data.cansat.vertical_velocity;

    if (data.cansat.accelerometer) {
      state.cs.ax = data.cansat.accelerometer.x || 0;
      state.cs.ay = data.cansat.accelerometer.y || 0;
      state.cs.az = data.cansat.accelerometer.z || 0;
    }

    if (data.cansat.gyroscope) {
      state.cs.gx = data.cansat.gyroscope.x || 0;
      state.cs.gy = data.cansat.gyroscope.y || 0;
      state.cs.gz = data.cansat.gyroscope.z || 0;
    }

    if (data.cansat.orientation) {
      state.cs.pitch = data.cansat.orientation.pitch || 0;
      state.cs.roll = data.cansat.orientation.roll || 0;
      state.cs.yaw = data.cansat.orientation.yaw || 0;
    }

    if (data.cansat.gas) {
      state.cs.ch4 = data.cansat.gas.ch4 || 0;
      state.cs.co = data.cansat.gas.co || 0;
      state.cs.nh3 = data.cansat.gas.nh3 || 0;
      state.cs.co2 = data.cansat.gas.co2 || 0;
    }

    if (data.cansat.gnss_latitude || data.cansat.gnss_longitude || data.cansat.gnss_sats || data.cansat.gnss_time) {
      state.cs.lat = data.cansat.gnss_latitude || 0;
      state.cs.lon = data.cansat.gnss_longitude || 0;
      state.cs.gpsAlt = data.cansat.gnss_altitude || 0;
      state.cs.sats = data.cansat.gnss_sats || 0;
      state.cs.gpsValid = true;
    }
  }

  if (data.terrain) {
    renderTerrainPanel(data.terrain);
  }

  if (isConnected) {
    // Live data: chart X axis = real seconds since the first packet, and a point is
    // plotted only when a NEW packet arrived (snapshots are broadcast at 10 Hz).
    const rpc = state.rkt.packetCount, cpc = state.cs.packetCount;
    if (rpc < lastChartPc.rkt || cpc < lastChartPc.cs) chartT0 = null;   // counter restarted
    const newPkt = (rpc !== lastChartPc.rkt || cpc !== lastChartPc.cs) && (rpc > 0 || cpc > 0);
    if (chartT0 === null) chartT0 = Date.now();
    state.t = (Date.now() - chartT0) / 1000;
    lastChartPc.rkt = rpc;
    lastChartPc.cs = cpc;
    updateTelemetryDisplay(false, newPkt);
  }
}

let socket = null;
function initSocketIO() {
  if (typeof io !== 'undefined') {
    try {
      const socketUrl = (location.protocol === 'file:' || !location.port || location.port !== '5000') ? "http://localhost:5000" : "";
      socket = socketUrl ? io(socketUrl) : io();
      socket.on("connect", () => {
        console.log("Socket.IO connected to GCS backend");
      });
      socket.on("telemetry_update", (snapshot) => {
        applySnapshotTelemetry(snapshot);
      });
      socket.on("serial_line", (m) => {
        if (!m) return;
        const role = m.device === 'rocket' ? 'rkt' : 'cs';
        if (m.ok && m.kind !== 'info') setText('dataStream', m.line);
        appendRawSerialLog(m.ok ? m.line : `${m.line}   <-- REJECTED: ${m.error}`, role);
      });
      socket.on("connection_status", (statusData) => {
        if (statusData) {
          setCommsStatus();
        }
      });
    } catch (err) {
      console.warn("Socket.IO initialization error:", err);
    }
  }
}
initSocketIO();

function clearIdleState() {
  ['rktAlt', 'rktPres', 'rktTemp', 'rktVel', 'csAlt', 'csPres', 'csTemp', 'csVel'].forEach(id => {
    if (gauges[id] && gauges[id].clearIdle) gauges[id].clearIdle();
  });

  const startBtn = document.getElementById('startCsvLogBtn');
  const stopBtn = document.getElementById('stopCsvLogBtn');
  if (startBtn) startBtn.classList.remove('idle-disabled');
  if (stopBtn) stopBtn.classList.add('idle-disabled');

  setMapIdleOverlay(false);
  setLeafletMarkersVisible(true);
  setOfflineMarkersVisible(true);

  const recvLed = document.getElementById('receiveLed');
  if (recvLed) recvLed.className = 'led orange';
}

setAwaitingTelemetry();
syncDefaultBaud();
bindBaudControls();
refreshGrantedPorts();
setInterval(refreshGrantedPorts, 3000);
['rocketPortSelect', 'cansatPortSelect'].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener('focus', refreshGrantedPorts);
});
if ('serial' in navigator) {
  navigator.serial.addEventListener('connect', refreshGrantedPorts);
  navigator.serial.addEventListener('disconnect', () => { refreshGrantedPorts(); if (!serialState.rkt.port && !serialState.cs.port) { isConnected = false; applyIdleState(); } });
}
setInterval(setCommsStatus, 1000);
updateCube('rkt-orientation-model', 0, 0, 0);
updateCube('cs-orientation-model', 0, 0, 0);
applyIdleState();
updateClock();
refreshGnssClock();

function injectTelemetryString() {
  const input = document.getElementById('telemetryInput');
  if (!input) return;
  const line = input.value.trim();
  if (!line) return;

  const parsed = parseTelemetryLine(line);
  const role = parsed ? parsed.role : 'rkt';

  applyTelemetry(line, role);
  appendRawSerialLog("Injected: " + line, 'INJECT');
  input.value = '';
}
