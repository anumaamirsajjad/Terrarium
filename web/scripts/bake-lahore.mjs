/**
 * Bake a slice of real Lahore into a static asset for the landing page's hero.
 *
 * Run once, by hand, when the extract needs refreshing:
 *
 *     node scripts/bake-lahore.mjs
 *
 * Why bake rather than fetch at runtime: the page must open with no network beyond its
 * own origin — that is the same rule that put the fonts in the bundle. Overpass is free
 * and keyless, so this costs nothing but a minute, and the result is a file the hero can
 * read instantly and offline.
 *
 * Geometry is © OpenStreetMap contributors, ODbL. The attribution is rendered in the hero
 * and must stay there; it is a licence condition, not a courtesy.
 *
 * Buildings are reduced to oriented boxes rather than kept as footprints. At the scale
 * this renders — a whole district in a hero — a box reads identically to an extruded
 * outline, costs one instanced draw call for all of them instead of several hundred
 * meshes, and shrinks the asset by an order of magnitude.
 */

import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const CENTRE = { lon: 74.35, lat: 31.5225 };
/** A ~2.8 km square of central Lahore, spanning the canal. */
const BBOX = [31.51, 74.335, 31.535, 74.365];

/** Metres per degree, equirectangular about the centre. Exact enough over 3 km. */
const M_PER_LAT = 111_320;
const M_PER_LON = 111_320 * Math.cos((CENTRE.lat * Math.PI) / 180);

/** Drop vertices closer together than this. Below a metre or two it is invisible here. */
const SIMPLIFY_M = 6;

/** Road classes, widest first. The weight drives ribbon width and brightness in the hero. */
const ROAD_CLASS = {
  motorway: 5,
  trunk: 5,
  primary: 4,
  secondary: 3,
  tertiary: 2,
  residential: 1,
  unclassified: 1,
};

const BB = BBOX.join(",");

/**
 * Everything the hero can place from real positions.
 *
 * A city is not buildings and roads. The extras below are what the eye actually uses to
 * decide it is looking at a place rather than at a diagram — a mosque on the skyline, a
 * cricket ground, a rail line cutting through, pylons marching across. All of them have
 * genuine coordinates in OSM, so none of it has to be invented.
 */
const QUERY = `
[out:json][timeout:240];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified)$"](${BB});
  way["waterway"~"^(canal|river|stream)$"](${BB});
  way["leisure"~"^(park|garden)$"](${BB});
  way["landuse"~"^(grass|forest|recreation_ground|cemetery|farmland)$"](${BB});
  way["building"](${BB});
  way["leisure"="pitch"](${BB});
  way["railway"="rail"](${BB});
  way["power"="line"](${BB});
  way["amenity"="place_of_worship"](${BB});
  node["amenity"="place_of_worship"](${BB});
  node["highway"="bus_stop"](${BB});
  node["amenity"="fuel"](${BB});
  node["power"="tower"](${BB});
  way["amenity"~"^(school|college|university|hospital|clinic|marketplace)$"](${BB});
  node["amenity"~"^(school|college|university|hospital|clinic|marketplace)$"](${BB});
  node["shop"](${BB});
);
out geom;
`;

/** Local metres, x east and z south, so north is -z the way a camera expects. */
function project({ lon, lat }) {
  return [(lon - CENTRE.lon) * M_PER_LON, -(lat - CENTRE.lat) * M_PER_LAT];
}

function simplify(points) {
  const out = [points[0]];
  for (const point of points.slice(1)) {
    const last = out.at(-1);
    if (Math.hypot(point[0] - last[0], point[1] - last[1]) >= SIMPLIFY_M) out.push(point);
  }
  // Never simplify a line out of existence: two points is the minimum that draws.
  if (out.length < 2) out.push(points.at(-1));
  return out;
}

const round = (value) => Math.round(value * 10) / 10;
const flatten = (points) => points.flat().map(round);

/**
 * A footprint reduced to its oriented bounding box.
 *
 * The orientation comes from the longest edge, which for a building is almost always its
 * true frontage — a full minimum-area-rectangle search costs more code for a difference
 * nobody can see at this zoom.
 */
function orientedBox(points) {
  let longest = 0;
  let angle = 0;
  for (let i = 0; i < points.length - 1; i++) {
    const dx = points[i + 1][0] - points[i][0];
    const dz = points[i + 1][1] - points[i][1];
    const length = Math.hypot(dx, dz);
    if (length > longest) {
      longest = length;
      angle = Math.atan2(dz, dx);
    }
  }

  const cos = Math.cos(-angle);
  const sin = Math.sin(-angle);
  let minU = Infinity;
  let maxU = -Infinity;
  let minV = Infinity;
  let maxV = -Infinity;
  for (const [x, z] of points) {
    const u = x * cos - z * sin;
    const v = x * sin + z * cos;
    minU = Math.min(minU, u);
    maxU = Math.max(maxU, u);
    minV = Math.min(minV, v);
    maxV = Math.max(maxV, v);
  }

  // Back to world space through the inverse rotation.
  const cu = (minU + maxU) / 2;
  const cv = (minV + maxV) / 2;
  return {
    x: round(cu * cos + cv * sin),
    z: round(-cu * sin + cv * cos),
    w: round(maxU - minU),
    d: round(maxV - minV),
    r: Math.round(angle * 1000) / 1000,
  };
}

function heightOf(tags, footprintArea) {
  const levels = Number(tags["building:levels"]);
  if (Number.isFinite(levels) && levels > 0) return round(levels * 3.2);
  const explicit = Number.parseFloat(tags.height);
  if (Number.isFinite(explicit) && explicit > 0) return round(explicit);
  // No tag: infer from footprint. Bigger plots in this district carry more storeys, and a
  // flat default reads as a car park rather than a city.
  return round(6 + Math.min(28, Math.sqrt(footprintArea) * 0.9));
}

/**
 * Overpass is a free shared service and it rations accordingly: 429 when it is rate
 * limiting, 504 when it is loaded. Both are routine and both clear on a retry, so the
 * script walks the public mirrors rather than failing a one-off bake on a busy minute.
 */
const MIRRORS = [
  "https://overpass-api.de/api/interpreter",
  "https://overpass.kumi.systems/api/interpreter",
  "https://overpass.osm.ch/api/interpreter",
];

async function query() {
  let lastError = "";
  for (let attempt = 0; attempt < 2; attempt++) {
    for (const mirror of MIRRORS) {
      process.stdout.write(`  ${new URL(mirror).host}… `);
      try {
        const response = await fetch(mirror, {
          method: "POST",
          // Overpass answers 406 to Node's default undici agent string. Identifying the
          // client is good manners on a shared service anyway.
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "terrarium-landing-bake/1.0 (https://github.com/otto-cr/terrarium)",
          },
          body: new URLSearchParams({ data: QUERY }),
        });
        if (!response.ok) {
          lastError = `${response.status} ${response.statusText}`;
          console.log(lastError);
          continue;
        }
        const { elements } = await response.json();
        console.log(`${elements.length} ways`);
        return elements;
      } catch (error) {
        lastError = String(error);
        console.log(lastError);
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 5000));
  }
  throw new Error(`every Overpass mirror refused: ${lastError}`);
}

async function main() {
  console.log("Querying Overpass…");
  const elements = await query();

  const roads = [];
  const water = [];
  const green = [];
  const buildings = [];
  const pitches = [];
  const rail = [];
  const power = [];
  const mosques = [];
  const stops = [];
  const fuel = [];
  const pylons = [];
  /** Campuses and institutions: {x, z, r, w, d, kind}. */
  const civic = [];
  const shops = [];

  const CIVIC = new Set(["school", "college", "university", "hospital", "clinic", "marketplace"]);

  /** A node's own position, or the centroid of a way's geometry. */
  const anchor = (element) => {
    if (element.type === "node") return project({ lon: element.lon, lat: element.lat });
    const points = element.geometry.map(project);
    const sum = points.reduce((acc, [x, z]) => [acc[0] + x, acc[1] + z], [0, 0]);
    return [round(sum[0] / points.length), round(sum[1] / points.length)];
  };

  for (const element of elements) {
    const tags = element.tags ?? {};

    if (element.type === "node") {
      const [x, z] = anchor(element);
      if (tags.amenity === "place_of_worship") mosques.push({ x, z, r: 0 });
      else if (tags.highway === "bus_stop") stops.push({ x, z });
      else if (tags.amenity === "fuel") fuel.push({ x, z });
      else if (tags.power === "tower") pylons.push({ x, z });
      else if (tags.shop) shops.push({ x, z });
      // A node-only campus has no footprint, so it gets a plausible one for its kind.
      else if (CIVIC.has(tags.amenity)) {
        const big = tags.amenity === "university" || tags.amenity === "college";
        civic.push({ x, z, r: 0, w: big ? 120 : 62, d: big ? 100 : 54, kind: tags.amenity });
      }
      continue;
    }

    if (!element.geometry || element.geometry.length < 2) continue;
    const points = simplify(element.geometry.map(project));

    if (tags.highway) {
      roads.push({ c: ROAD_CLASS[tags.highway] ?? 1, p: flatten(points) });
    } else if (tags.waterway) {
      water.push({ p: flatten(points) });
    } else if (tags.railway === "rail") {
      rail.push({ p: flatten(points) });
    } else if (tags.power === "line") {
      power.push({ p: flatten(points) });
    } else if (tags.amenity === "place_of_worship") {
      const box = orientedBox(points);
      mosques.push({ x: box.x, z: box.z, r: box.r, w: box.w, d: box.d });
    } else if (CIVIC.has(tags.amenity)) {
      const box = orientedBox(points);
      civic.push({ ...box, kind: tags.amenity });
    } else if (tags.leisure === "pitch") {
      const box = orientedBox(points);
      if (box.w >= 12 && box.d >= 12) pitches.push(box);
    } else if (tags.building) {
      const box = orientedBox(points);
      // Discard slivers and mapping noise; they add instances and read as grit.
      if (box.w < 4 || box.d < 4) continue;
      buildings.push({ ...box, h: heightOf(tags, box.w * box.d) });
    } else {
      green.push({ p: flatten(points) });
    }
  }

  const payload = {
    meta: {
      centre: [CENTRE.lon, CENTRE.lat],
      bbox: BBOX,
      source: "© OpenStreetMap contributors, ODbL",
      fetched: new Date().toISOString().slice(0, 10),
      units: "metres, local frame, +x east / +z south",
    },
    roads,
    water,
    green,
    buildings,
    pitches,
    rail,
    power,
    mosques,
    stops,
    fuel,
    pylons,
    civic,
    shops,
  };

  const out = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "lahore.json");
  writeFileSync(out, JSON.stringify(payload));
  console.log(
    `roads ${roads.length} · water ${water.length} · green ${green.length} · ` +
      `buildings ${buildings.length} · pitches ${pitches.length} · rail ${rail.length} · ` +
      `power ${power.length} · mosques ${mosques.length} · stops ${stops.length} · ` +
      `fuel ${fuel.length} · pylons ${pylons.length} · civic ${civic.length} · shops ${shops.length}`,
  );
  console.log(`wrote ${out} (${(JSON.stringify(payload).length / 1024).toFixed(0)} kB)`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
