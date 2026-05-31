getAuthedUser().then((user) => {
  /*
    Any component that needs user info, or the lack thereof, should listen for
    this event. Example: 
    document.addEventListener("afterAuthCheck", (e) => {
      const { user } = e.detail;
      console.log(user); // { did: "1234", handle: "gooddog.bsky.social" }  
    })
  */
  document.dispatchEvent(
    new CustomEvent("afterAuthCheck", { detail: { user } })
  );
});

/**
 * @returns {Promise<{did: string, handle: string}|null>} The authenticated user, or null if not authenticated.
 */
async function getAuthedUser() {
  /*
    Look for a cached user object in localstorage, if found, use it instead of
    making a trip to the server. If any other auth-required request fails with
    a 401, we clear the cache/logout.
  */
  const cachedUser = localStorage.getItem("auth:user");
  try {
    if (cachedUser) {
      const user = JSON.parse(cachedUser);
      // Wait until the next loop to make sure components are in the DOM.
      await sleep(0);
      return user;
    }
  } catch (e) {
    console.warn(e);
  }

  try {
    const res = await fetch(`${API_BASE}/oauth/me`, FETCH_OPTS);
    if (res.ok) {
      const user = await res.json();
      // Cache the user object so we don't need to fetch it on every page.
      localStorage.setItem("auth:user", JSON.stringify(user));
      return user;
    }
  } catch (e) {
    // FIXME: Handle fetch failure
    console.log(e.message);
    return null;
  }

  return null;
}

/**
 * @param {string} handle
 * @returns {Promise<{did: string, handle: string}|null>} The resolved user, or null if not found.
 */
async function getUserByHandle(handle) {
  try {
    const res = await fetch(
      `${API_BASE}/api/resolve/${encodeURIComponent(handle)}`,
      FETCH_OPTS
    );

    if (!res.ok) throw new Error("Could not resolve handle");

    const user = await res.json();
    return user;
  } catch (err) {
    return null;
  }
}

/**
 * @param {string} [did]
 * @returns {Promise<{data: Array|null, error: string|null}>}
 */
async function getActivities(did, { limit, offset } = {}) {
  const path = did
    ? `/api/activities/${encodeURIComponent(did)}`
    : "/api/activities";
  const params = new URLSearchParams();
  if (limit) params.set("limit", limit);
  if (offset) params.set("offset", offset);
  const qs = params.toString();
  const url = `${API_BASE}${path}${qs ? `?${qs}` : ""}`;

  try {
    const res = await fetch(url, FETCH_OPTS);
    if (!res.ok)
      return {
        data: null,
        error: `HTTP ${res.status}`,
      };
    const data = await res.json();
    return {
      data,
      error: null,
    };
  } catch (err) {
    return {
      data: null,
      error: err.message,
    };
  }
}

/**
 * @param {string} did
 * @param {string} rkey
 * @returns {Promise<{data: Object|null, error: string|null}>}
 */
async function getActivity(did, rkey) {
  const url = `${API_BASE}/api/activities/${encodeURIComponent(
    did
  )}/${encodeURIComponent(rkey)}`;

  try {
    const res = await fetch(url, FETCH_OPTS);
    if (!res.ok)
      return {
        data: null,
        error: `HTTP ${res.status}`,
      };
    const data = await res.json();
    return {
      data,
      error: null,
    };
  } catch (err) {
    return {
      data: null,
      error: err.message,
    };
  }
}

function metersToMiles(m) {
  return (parseFloat(m) / 1609.344).toFixed(1);
}

function msToMph(ms) {
  return (parseFloat(ms) * 2.23694).toFixed(1);
}

function metersToFeet(m) {
  return Math.round(parseFloat(m) * 3.28084);
}

function formatDuration(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * Decode a Google encoded polyline string into an array of [lng, lat] pairs.
 * @param {string} encoded
 * @returns {Array<[number, number]>}
 */
function decodePolyline(encoded) {
  const coords = [];
  let i = 0;
  let lat = 0;
  let lng = 0;

  while (i < encoded.length) {
    let shift = 0;
    let result = 0;
    let byte;

    do {
      byte = encoded.charCodeAt(i++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20);

    lat += result & 1 ? ~(result >> 1) : result >> 1;

    shift = 0;
    result = 0;

    do {
      byte = encoded.charCodeAt(i++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20);

    lng += result & 1 ? ~(result >> 1) : result >> 1;

    coords.push([lng / 1e5, lat / 1e5]);
  }

  return coords;
}

/**
 * Convert an encoded polyline to an SVG path string.
 * @param {string} encoded - Google encoded polyline
 * @param {number} [padding=10] - Padding around the path in SVG units
 * @returns {string} SVG markup string, or empty string if no valid coords
 */
function polylineToSVG(encoded) {
  const coords = decodePolyline(encoded);
  if (coords.length < 2) return "";

  // cos(lat) correction for longitude
  const midLat = coords.reduce((s, c) => s + c[1], 0) / coords.length;
  const cosLat = Math.cos((midLat * Math.PI) / 180);

  // Project and flip Y (SVG Y is top-down, lat is bottom-up)
  const projected = coords.map(([lng, lat]) => [lng * cosLat, -lat]);

  let minX = Infinity,
    minY = Infinity,
    maxX = -Infinity,
    maxY = -Infinity;
  for (const [x, y] of projected) {
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }

  const rangeX = maxX - minX || 0.001;
  const rangeY = maxY - minY || 0.001;
  const pad = Math.max(rangeX, rangeY) * 0.05;

  const points = projected.map(([x, y]) => `${x},${y}`).join(" ");

  return `<svg viewBox="${minX - pad} ${minY - pad} ${rangeX + pad * 2} ${
    rangeY + pad * 2
  }" preserveAspectRatio="xMidYMid meet" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">
    <polyline points="${points}" fill="none" stroke="currentColor" stroke-width="3" vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round" />
  </svg>`;
}

function formatDate(iso) {
  return new Date(iso).toLocaleDateString("en-US", {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function getActivityStats(activity) {
  const hasDistance = parseInt(activity.distance, 10) > 0;
  let stats = [];

  if (hasDistance) {
    stats.push({
      label: "Distance",
      value: `${metersToMiles(activity.distance)} mi`,
    });
  }

  stats.push({
    label: "Moving Time",
    value: formatDuration(activity.moving_time),
  });

  if (activity.elevation_gain) {
    stats.push({
      label: "Elev Gain",
      value: `${metersToFeet(activity.elevation_gain)} ft`,
    });
  }
  if (activity.elevation_loss) {
    stats.push({
      label: "Elev Loss",
      value: `${metersToFeet(activity.elevation_loss)} ft`,
    });
  }
  if (activity.avg_speed) {
    stats.push({
      label: "Avg Speed",
      value: `${msToMph(activity.avg_speed)} mph`,
    });
  }
  if (activity.max_speed) {
    stats.push({
      label: "Max Speed",
      value: `${msToMph(activity.max_speed)} mph`,
    });
  }
  if (activity.avg_heart_rate) {
    stats.push({ label: "Avg HR", value: `${activity.avg_heart_rate} bpm` });
  }
  if (activity.max_heart_rate) {
    stats.push({ label: "Max HR", value: `${activity.max_heart_rate} bpm` });
  }
  if (activity.avg_cadence) {
    stats.push({ label: "Avg Cadence", value: `${activity.avg_cadence}` });
  }
  if (activity.avg_power) {
    stats.push({ label: "Avg Power", value: `${activity.avg_power} W` });
  }
  if (activity.max_power) {
    stats.push({ label: "Max Power", value: `${activity.max_power} W` });
  }
  if (activity.calories) {
    stats.push({ label: "Calories", value: `${activity.calories}` });
  }

  return stats;
}

function pluralize(n, singular, plural) {
  return n === 1 ? singular : plural;
}

function capitalize(str) {
  return str.charAt(0).toUpperCase() + str.slice(1);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
