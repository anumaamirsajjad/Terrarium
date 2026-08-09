/**
 * Everything in a city that is not a building or a road.
 *
 * A district drawn as extruded blocks with tarmac between them reads as a diagram, because
 * the things a person actually uses to recognise a city are the small ones: a mosque on
 * the skyline, streetlights down a carriageway, cars, a wall around a plot, a billboard, a
 * dish on a roof, a stall under a canopy, a cricket ground, a rail line, a pylon.
 *
 * Fifteen kinds of object are placed here, and they resolve to **three primitive shapes** —
 * box, cylinder, sphere — so the whole lot renders in three instanced draw calls rather
 * than fifteen. A prop is a position, a size, a rotation and a colour; what it *means* is
 * decided here and forgotten by the time it reaches the GPU.
 *
 * Where OSM knows the real position — mosques, pitches, rail, pylons, fuel, bus stops —
 * that is used. Everything else is derived from the real street network rather than
 * scattered: streetlights follow carriageways, stalls gather at junctions, walls enclose
 * blocks the roads already define.
 */

import {
  type BuildingBox,
  CELL,
  EXTENT,
  FILL_RADIUS,
  type LahoreExtract,
  type Occupancy,
  campusFootprint,
  hash,
  walk,
} from "./lahore";

/** A unit primitive, placed. Colour is linear 0-1 RGB. */
export interface Prop {
  x: number;
  y: number;
  z: number;
  w: number;
  h: number;
  d: number;
  /** Rotation about Y, radians. */
  r: number;
  c: [number, number, number];
}

export interface CityProps {
  boxes: Prop[];
  cylinders: Prop[];
  spheres: Prop[];
}

const box = (
  x: number,
  y: number,
  z: number,
  w: number,
  h: number,
  d: number,
  r: number,
  c: [number, number, number],
): Prop => ({ x, y, z, w, h, d, r, c });

// --------------------------------------------------------------- palettes ---

const WALL: [number, number, number] = [0.36, 0.33, 0.29];
const CONCRETE: [number, number, number] = [0.55, 0.52, 0.47];
const DARK_METAL: [number, number, number] = [0.22, 0.22, 0.24];
const LAMP: [number, number, number] = [0.95, 0.82, 0.55];
const PITCH_GREEN: [number, number, number] = [0.17, 0.31, 0.16];
const DOME: [number, number, number] = [0.62, 0.66, 0.68];

/** Awning colours for a market row. Bright, because a bazaar is. */
const AWNINGS: [number, number, number][] = [
  [0.62, 0.24, 0.2],
  [0.24, 0.4, 0.55],
  [0.68, 0.55, 0.22],
  [0.3, 0.45, 0.3],
  [0.55, 0.45, 0.5],
];

// ------------------------------------------------------------------ props ---

export function buildProps(
  data: LahoreExtract,
  occupancy: Occupancy,
  buildings: BuildingBox[],
): CityProps {
  const boxes: Prop[] = [];
  const cylinders: Prop[] = [];
  const spheres: Prop[] = [];

  // 1. Mosques — a domed prayer hall and two minarets. The one silhouette that says
  //    which city this is, and OSM has twelve real positions inside the extract.
  for (const mosque of data.mosques ?? []) {
    const width = Math.max(16, Math.min(34, mosque.w ?? 22));
    const depth = Math.max(14, Math.min(30, mosque.d ?? 18));
    boxes.push(box(mosque.x, 5, mosque.z, width, 10, depth, mosque.r, [0.7, 0.67, 0.6]));
    spheres.push({
      x: mosque.x,
      y: 11.5,
      z: mosque.z,
      w: Math.min(width, depth) * 0.52,
      h: Math.min(width, depth) * 0.46,
      d: Math.min(width, depth) * 0.52,
      r: 0,
      c: DOME,
    });
    for (const side of [-1, 1]) {
      const offsetX = Math.cos(mosque.r) * (width / 2 + 1.5) * side;
      const offsetZ = -Math.sin(mosque.r) * (width / 2 + 1.5) * side;
      cylinders.push({
        x: mosque.x + offsetX,
        y: 13,
        z: mosque.z + offsetZ,
        w: 2.4,
        h: 26,
        d: 2.4,
        r: 0,
        c: [0.72, 0.69, 0.62],
      });
      spheres.push({
        x: mosque.x + offsetX,
        y: 27,
        z: mosque.z + offsetZ,
        w: 3,
        h: 3.4,
        d: 3,
        r: 0,
        c: DOME,
      });
    }
  }

  // 2. Sports pitches — a flat green rectangle with a lighter strip down the middle,
  //    which at this scale is unmistakably a cricket ground.
  for (const pitch of data.pitches ?? []) {
    boxes.push(box(pitch.x, 0.3, pitch.z, pitch.w, 0.6, pitch.d, pitch.r, PITCH_GREEN));
    boxes.push(
      box(pitch.x, 0.7, pitch.z, Math.min(pitch.w, pitch.d) * 0.14, 0.4, pitch.d * 0.6, pitch.r, [
        0.5, 0.46, 0.36,
      ]),
    );
  }

  // 3. Rail — ballast with two rails on it.
  for (const line of data.rail ?? []) {
    walk(line.p, 9, (x, z, dirX, dirZ) => {
      const angle = Math.atan2(dirZ, dirX);
      boxes.push(box(x, 0.5, z, 9.5, 0.8, 5, -angle, [0.24, 0.22, 0.2]));
      for (const side of [-1, 1]) {
        boxes.push(
          box(x - dirZ * 0.8 * side, 1, z + dirX * 0.8 * side, 9.5, 0.4, 0.4, -angle, [
            0.42, 0.4, 0.38,
          ]),
        );
      }
    });
  }

  // 4. Pylons — a tapered lattice with two cross arms. Placed on OSM's towers, and along
  //    any mapped power line, because four towers alone read as an accident.
  const pylonSpots = [...(data.pylons ?? [])];
  for (const line of data.power ?? []) {
    walk(line.p, 210, (x, z) => pylonSpots.push({ x, z }));
  }
  for (const spot of pylonSpots) {
    cylinders.push({ x: spot.x, y: 16, z: spot.z, w: 3.4, h: 32, d: 3.4, r: 0, c: DARK_METAL });
    for (const level of [22, 28]) {
      boxes.push(box(spot.x, level, spot.z, 15, 0.7, 1, 0, DARK_METAL));
    }
  }

  // 5. Fuel stations — a canopy on posts.
  for (const station of data.fuel ?? []) {
    boxes.push(box(station.x, 6.4, station.z, 18, 0.9, 12, 0, [0.78, 0.76, 0.7]));
    for (const [dx, dz] of [
      [-7, -4],
      [7, -4],
      [-7, 4],
      [7, 4],
    ]) {
      cylinders.push({
        x: station.x + dx,
        y: 3,
        z: station.z + dz,
        w: 0.8,
        h: 6,
        d: 0.8,
        r: 0,
        c: CONCRETE,
      });
    }
  }

  // 16-19. Schools, colleges, universities, hospitals, clinics and marketplaces — twenty
  //        of them, at OSM's real positions. Each is a *campus*, not a box: institutions
  //        are the largest plots in any district, and drawing them as one more extrusion
  //        is exactly what left the frame looking half empty.
  for (const campus of data.civic ?? []) {
    buildCampus(campus, boxes, cylinders, spheres);
  }

  // 20. Shops — a unit with an awning over the pavement, at 88 real positions.
  for (const shop of data.shops ?? []) {
    const angle = hash(shop.x, shop.z, 90) * Math.PI;
    boxes.push(box(shop.x, 2.4, shop.z, 9, 4.8, 8, angle, [0.58, 0.53, 0.46]));
    boxes.push(
      box(
        shop.x + Math.cos(angle) * 5.4,
        3.1,
        shop.z - Math.sin(angle) * 5.4,
        3,
        0.3,
        7,
        angle,
        AWNINGS[Math.floor(hash(shop.x, shop.z, 91) * AWNINGS.length)]!,
      ),
    );
  }

  // ---- derived from the street network -------------------------------------

  const major = data.roads.filter((line) => (line.c ?? 1) >= 3);
  const minor = data.roads.filter((line) => (line.c ?? 1) === 2);

  // 6. Streetlights. Nothing says "carriageway" faster than a row of poles at an even
  //    pitch, and the lamp heads catch the sun as a line of bright dots.
  for (const line of major) {
    walk(line.p, 42, (x, z, dirX, dirZ) => {
      if (hash(x, z, 61) > 0.85) return;
      for (const side of [-1, 1]) {
        const px = x - dirZ * 9 * side;
        const pz = z + dirX * 9 * side;
        cylinders.push({ x: px, y: 4.5, z: pz, w: 0.34, h: 9, d: 0.34, r: 0, c: DARK_METAL });
        boxes.push(
          box(px + dirZ * 1.4 * side, 9.1, pz - dirX * 1.4 * side, 2.4, 0.4, 0.7, 0, LAMP),
        );
      }
    });
  }

  // 7. Moving traffic is elsewhere (it has to be animated); this is everything parked.
  for (const line of [...major, ...minor]) {
    walk(line.p, 13, (x, z, dirX, dirZ) => {
      if (hash(x, z, 62) > 0.42) return;
      const side = hash(x, z, 63) > 0.5 ? 1 : -1;
      const angle = Math.atan2(dirZ, dirX);
      const tone = hash(x, z, 64);
      boxes.push(
        box(x - dirZ * 6.4 * side, 0.8, z + dirX * 6.4 * side, 4.2, 1.5, 1.8, -angle, [
          0.22 + tone * 0.55,
          0.22 + tone * 0.52,
          0.24 + tone * 0.5,
        ]),
      );
    });
  }

  // 8. Bus shelters. OSM has one inside the extract, so the rest go where they would be:
  //    on the big roads, at a spacing a rider would recognise.
  for (const stop of data.stops ?? []) {
    boxes.push(box(stop.x, 1.6, stop.z, 6, 0.4, 2.6, 0, [0.5, 0.48, 0.44]));
  }
  for (const line of major) {
    walk(line.p, 260, (x, z, dirX, dirZ) => {
      if (hash(x, z, 65) > 0.55) return;
      const angle = Math.atan2(dirZ, dirX);
      boxes.push(
        box(x - dirZ * 10, 3.1, z + dirX * 10, 6.5, 0.4, 2.8, -angle, [0.5, 0.48, 0.44]),
      );
      boxes.push(
        box(x - dirZ * 10, 1.5, z + dirX * 10, 6.5, 3, 0.3, -angle, [0.32, 0.34, 0.36]),
      );
    });
  }

  // 9. Billboards — a panel on two legs, angled to the traffic.
  for (const line of major) {
    walk(line.p, 320, (x, z, dirX, dirZ) => {
      if (hash(x, z, 66) > 0.5) return;
      const angle = Math.atan2(dirZ, dirX);
      const px = x - dirZ * 13;
      const pz = z + dirX * 13;
      cylinders.push({ x: px, y: 4, z: pz, w: 0.6, h: 8, d: 0.6, r: 0, c: DARK_METAL });
      boxes.push(
        box(px, 10, pz, 11, 5, 0.4, -angle, [
          0.4 + hash(x, z, 67) * 0.35,
          0.34 + hash(x, z, 68) * 0.3,
          0.3 + hash(x, z, 69) * 0.35,
        ]),
      );
    });
  }

  // 10. Market rows — awnings gathered where two big roads meet, the way a bazaar does.
  for (const line of major) {
    walk(line.p, 150, (x, z, dirX, dirZ) => {
      if (hash(x, z, 70) > 0.34) return;
      const angle = Math.atan2(dirZ, dirX);
      for (let i = 0; i < 5; i++) {
        const along = (i - 2) * 5.4;
        const colour = AWNINGS[Math.floor(hash(x + i, z, 71) * AWNINGS.length)]!;
        boxes.push(
          box(
            x + dirX * along - dirZ * 12,
            2.9,
            z + dirZ * along + dirX * 12,
            5,
            0.35,
            4.4,
            -angle,
            colour,
          ),
        );
      }
    });
  }

  // 11. Boundary walls. Almost every plot in this city has one, and a low line around a
  //     block is what turns a cluster of boxes into properties on a street.
  for (const line of [...major, ...minor]) {
    walk(line.p, 6, (x, z, dirX, dirZ) => {
      if (hash(x, z, 72) > 0.5) return;
      const angle = Math.atan2(dirZ, dirX);
      const side = hash(x, z, 73) > 0.5 ? 1 : -1;
      boxes.push(
        box(x - dirZ * 11 * side, 1.1, z + dirX * 11 * side, 6, 2.2, 0.5, -angle, WALL),
      );
    });
  }

  // 12. Satellite dishes and 13. rooftop clutter. Both go on real roofs, so the skyline
  //     stops being a field of clean rectangles.
  for (const building of buildings) {
    if (Math.min(building.w, building.d) < 11) continue;
    const roll = hash(building.x, building.z, 74);
    if (roll < 0.5) {
      spheres.push({
        x: building.x + (hash(building.x, building.z, 75) - 0.5) * building.w * 0.5,
        y: building.h + 1.2,
        z: building.z + (hash(building.x, building.z, 76) - 0.5) * building.d * 0.5,
        w: 2.4,
        h: 1.2,
        d: 2.4,
        r: 0,
        c: [0.72, 0.7, 0.66],
      });
    }
    if (roll > 0.72) {
      // A stair head-house. Every flat roof in the city has one.
      boxes.push(
        box(
          building.x + building.w * 0.22,
          building.h + 1.5,
          building.z - building.d * 0.2,
          3.4,
          3,
          3,
          building.r,
          [0.5, 0.47, 0.43],
        ),
      );
    }
  }

  // 14. Park ground — a flat green pad under the mapped greenery, so a park is a surface
  //     and not merely an absence of buildings.
  for (const area of data.green) {
    walk(area.p, 30, (x, z) => {
      // Light enough to read as grass under this sun. At the previous value a park was
      // indistinguishable from the gaps between buildings, so the biggest open spaces in
      // the frame looked like holes in the model.
      const tone = hash(x, z, 77);
      boxes.push(
        box(x, 0.15, z, 34, 0.3, 34, 0, [0.19 + tone * 0.05, 0.31 + tone * 0.07, 0.17]),
      );
    });
  }

  // 15. Bridges — a deck wherever a mapped road crosses the canal.
  for (const line of data.water) {
    walk(line.p, 24, (x, z, dirX, dirZ) => {
      if (!isRoadNear(occupancy, x, z)) return;
      const angle = Math.atan2(dirZ, dirX);
      boxes.push(box(x, 2.2, z, 12, 1.2, 34, -angle, [0.44, 0.42, 0.39]));
    });
  }

  return {
    boxes: boxes.filter(inside),
    cylinders: cylinders.filter(inside),
    spheres: spheres.filter(inside),
  };
}

function inside(prop: Prop): boolean {
  return Math.hypot(prop.x, prop.z) < FILL_RADIUS;
}

/**
 * A campus, built from its kind.
 *
 * Institutions do not look like the housing around them and they are what a district's
 * open space is actually made of — a school has a yard, a hospital has a forecourt and a
 * block taller than its neighbours, a university has ranges around a quad. Each is a
 * handful of primitives arranged in the plan its type implies.
 */
function buildCampus(
  campus: { x: number; z: number; r: number; w: number; d: number; kind: string },
  boxes: Prop[],
  cylinders: Prop[],
  spheres: Prop[],
) {
  const { x, z, r, kind } = campus;
  // Capped hard. A mapped campus boundary can be hundreds of metres across, and building
  // the plan at that size produced a single pale mass wider than a city block — legible
  // as a shape, not as a place. An institution should be conspicuous, not dominant.
  // The cap lives in `lahore.ts` because the occupancy grid has to apply the same one:
  // when only the drawing was capped, the exclusion still reserved the raw boundary and
  // the infill left a square kilometre of bare ground around one small building.
  const { w, d } = campusFootprint(campus);
  const cos = Math.cos(r);
  const sin = Math.sin(r);
  /** Local plan coordinates into world space. */
  const at = (u: number, v: number): [number, number] => [x + u * cos + v * sin, z - u * sin + v * cos];

  if (kind === "hospital" || kind === "clinic") {
    const height = kind === "hospital" ? 26 : 11;
    const [bx, bz] = at(0, -d * 0.12);
    boxes.push(box(bx, height / 2, bz, w * 0.66, height, d * 0.5, r, [0.66, 0.66, 0.62]));
    // A wing, so it is not a single slab.
    const [wx, wz] = at(-w * 0.3, d * 0.24);
    boxes.push(box(wx, 5, wz, w * 0.34, 10, d * 0.34, r, [0.6, 0.6, 0.57]));
    // Forecourt, and the one red mark in the district.
    const [fx, fz] = at(0, d * 0.34);
    boxes.push(box(fx, 0.25, fz, w * 0.6, 0.5, d * 0.2, r, [0.3, 0.29, 0.27]));
    boxes.push(box(bx, height + 0.6, bz, 5, 0.5, 1.6, r, [0.66, 0.16, 0.15]));
    boxes.push(box(bx, height + 0.6, bz, 1.6, 0.5, 5, r, [0.66, 0.16, 0.15]));
    return;
  }

  if (kind === "university" || kind === "college") {
    // Ranges around a quad, which is what a campus is from the air.
    for (const [u, v, uw, vd] of [
      [0, -d * 0.36, w * 0.8, d * 0.2],
      [0, d * 0.36, w * 0.8, d * 0.2],
      [-w * 0.36, 0, w * 0.18, d * 0.55],
      [w * 0.36, 0, w * 0.18, d * 0.55],
    ]) {
      const [px, pz] = at(u!, v!);
      boxes.push(box(px, 7.5, pz, uw!, 15, vd!, r, [0.6, 0.56, 0.48]));
    }
    const [qx, qz] = at(0, 0);
    boxes.push(box(qx, 0.25, qz, w * 0.5, 0.5, d * 0.45, r, [0.21, 0.33, 0.18]));
    // A clock tower on the main range. Every campus has one and it reads at distance.
    const [tx, tz] = at(0, -d * 0.36);
    cylinders.push({ x: tx, y: 14, z: tz, w: 7, h: 28, d: 7, r: 0, c: [0.72, 0.66, 0.56] });
    spheres.push({ x: tx, y: 29, z: tz, w: 7, h: 5, d: 7, r: 0, c: DOME });
    return;
  }

  if (kind === "marketplace") {
    for (let row = -1; row <= 1; row++) {
      for (let i = -2; i <= 2; i++) {
        const [px, pz] = at(i * (w / 6), row * (d / 3.4));
        boxes.push(
          box(px, 2.6, pz, w / 7, 0.35, d / 4.2, r, AWNINGS[Math.abs((i + row * 2) % AWNINGS.length)]!),
        );
      }
    }
    return;
  }

  // Schools: an L of teaching blocks around a yard, with a pitch on it.
  const [ax, az] = at(-w * 0.18, -d * 0.28);
  boxes.push(box(ax, 5.5, az, w * 0.62, 11, d * 0.22, r, [0.7, 0.6, 0.46]));
  const [sx, sz] = at(-w * 0.36, d * 0.14);
  boxes.push(box(sx, 5.5, sz, w * 0.2, 11, d * 0.42, r, [0.7, 0.6, 0.46]));
  const [yx, yz] = at(w * 0.16, d * 0.16);
  boxes.push(box(yx, 0.25, yz, w * 0.5, 0.5, d * 0.5, r, [0.36, 0.31, 0.24]));
}

/** Is a mapped road within a cell or two? Used to find canal crossings. */
function isRoadNear(occupancy: Occupancy, x: number, z: number): boolean {
  if (Math.abs(x) >= EXTENT || Math.abs(z) >= EXTENT) return false;
  const grid = Math.ceil((EXTENT * 2) / CELL);
  const gx = Math.floor((x + EXTENT) / CELL);
  const gz = Math.floor((z + EXTENT) / CELL);
  for (let dz = -1; dz <= 1; dz++) {
    for (let dx = -1; dx <= 1; dx++) {
      const nx = gx + dx;
      const nz = gz + dz;
      if (nx < 0 || nz < 0 || nx >= grid || nz >= grid) continue;
      if (occupancy.road[nz * grid + nx]) return true;
    }
  }
  return false;
}

// --------------------------------------------------------------- traffic ---

export interface Vehicle {
  /** Flat polyline the vehicle follows. */
  path: number[];
  /** Metres travelled along it. */
  at: number;
  speed: number;
  /** Perpendicular offset — which side of the centreline it drives on. */
  lane: number;
  length: number;
  width: number;
  height: number;
  c: [number, number, number];
}

/** Muted body colours, plus the yellow-and-black of a Lahore rickshaw. */
const BODIES: [number, number, number][] = [
  [0.72, 0.72, 0.74],
  [0.18, 0.19, 0.21],
  [0.5, 0.51, 0.54],
  [0.45, 0.16, 0.15],
  [0.2, 0.28, 0.42],
  [0.78, 0.66, 0.2],
];

/** Total length of a flat polyline, metres. */
function pathLength(points: number[]): number {
  let total = 0;
  for (let i = 0; i + 3 < points.length; i += 2) {
    total += Math.hypot(points[i + 2]! - points[i]!, points[i + 3]! - points[i + 1]!);
  }
  return total;
}

/**
 * Traffic on the real network.
 *
 * Movement is the single strongest cue that a scene is a simulation rather than a model —
 * a still city is a diorama however much detail it has. Vehicles are assigned to mapped
 * roads weighted by class, so the big carriageways carry most of it.
 */
export function buildTraffic(data: LahoreExtract, count = 260): Vehicle[] {
  const candidates = data.roads.filter((line) => (line.c ?? 1) >= 2 && line.p.length >= 6);
  if (candidates.length === 0) return [];

  const vehicles: Vehicle[] = [];
  for (let i = 0; i < count; i++) {
    const line = candidates[Math.floor(hash(i, 1, 81) * candidates.length) % candidates.length]!;
    const length = pathLength(line.p);
    if (length < 60) continue;

    const kind = hash(i, 2, 82);
    const bus = kind > 0.9;
    const rickshaw = !bus && kind > 0.72;

    vehicles.push({
      path: line.p,
      at: hash(i, 3, 83) * length,
      // Buses lumber, rickshaws buzz. Metres per second, roughly.
      speed: (bus ? 7 : rickshaw ? 9 : 12) * (0.75 + hash(i, 4, 84) * 0.5),
      lane: (hash(i, 5, 85) > 0.5 ? 1 : -1) * 3.4,
      length: bus ? 11 : rickshaw ? 3 : 4.4,
      width: bus ? 2.6 : rickshaw ? 1.5 : 1.9,
      height: bus ? 3.2 : rickshaw ? 1.9 : 1.5,
      c: bus
        ? [0.85, 0.85, 0.82]
        : rickshaw
          ? [0.75, 0.68, 0.18]
          : BODIES[Math.floor(hash(i, 6, 86) * BODIES.length)]!,
    });
  }

  return vehicles;
}

/** Position and heading at a distance along a flat polyline. */
export function samplePath(
  points: number[],
  distance: number,
): { x: number; z: number; angle: number; total: number } {
  let travelled = 0;
  for (let i = 0; i + 3 < points.length; i += 2) {
    const ax = points[i]!;
    const az = points[i + 1]!;
    const bx = points[i + 2]!;
    const bz = points[i + 3]!;
    const segment = Math.hypot(bx - ax, bz - az);
    if (travelled + segment >= distance) {
      const t = segment > 0 ? (distance - travelled) / segment : 0;
      return {
        x: ax + (bx - ax) * t,
        z: az + (bz - az) * t,
        angle: Math.atan2(bz - az, bx - ax),
        total: 0,
      };
    }
    travelled += segment;
  }
  return { x: points[0]!, z: points[1]!, angle: 0, total: travelled };
}
