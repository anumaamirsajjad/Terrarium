/**
 * Turning the baked Lahore extract into things a scene can draw.
 *
 * The asset carries real OSM geometry for a ~2.8 km square of central Lahore: road
 * centrelines, the canal, parks, and 736 building footprints reduced to oriented boxes.
 * Real coverage is sparse — OSM's building data for this district is partial — so the
 * blocks between roads are filled in procedurally. Every real footprint that exists is
 * used; the infill only occupies ground nothing is mapped on.
 *
 * All the spatial questions here ("is this on a road?", "how built-up is it around
 * here?") are answered from one coarse occupancy grid rather than by testing points
 * against 562 polylines. Building it is a single pass over the geometry; every query
 * afterwards is an array lookup.
 */

export interface Polyline {
  /** Flat [x, z, x, z, …] in metres, +x east and +z south. */
  p: number[];
  /** Road class, 1 (residential) to 5 (motorway). Absent on water and green. */
  c?: number;
}

export interface BuildingBox {
  x: number;
  z: number;
  /** Along the box's own long axis, before rotation. */
  w: number;
  d: number;
  /** Rotation about Y, radians. */
  r: number;
  h: number;
}

/** A point feature OSM knows the real position of. */
export interface Spot {
  x: number;
  z: number;
}

export interface LahoreExtract {
  meta: { centre: [number, number]; bbox: number[]; source: string; fetched: string };
  roads: Polyline[];
  water: Polyline[];
  green: Polyline[];
  buildings: BuildingBox[];
  /** Sports grounds, as oriented boxes. */
  pitches?: BuildingBox[];
  rail?: Polyline[];
  power?: Polyline[];
  /** Prayer halls. `w`/`d` present when the source was a mapped footprint. */
  mosques?: { x: number; z: number; r: number; w?: number; d?: number }[];
  stops?: Spot[];
  fuel?: Spot[];
  pylons?: Spot[];
  /** Schools, colleges, universities, hospitals, clinics, marketplaces. */
  civic?: (BuildingBox & { kind: string })[];
  shops?: Spot[];
}

/** Half-width of the real extract, metres. The bake covers 2.8 km; this is half of it. */
export const EXTENT = 1400;

/**
 * How far the procedural surround reaches, metres.
 *
 * The scene rotates about its own centre, so its extent has to be a **disc** rather than
 * the square the extract came in. A square sweeps its corners through the frustum and
 * shows the void past its edges on every quarter turn; a disc larger than anything the
 * camera can see is rotation-invariant by construction. The fog then lands well inside
 * this, so the boundary is never the thing that ends the city — distance is.
 */
export const FILL_RADIUS = 2900;

/** Block pitch of the procedural street grid beyond the real data, metres. */
const BLOCK = 250;
/** Width of those streets. Buildings are kept out of this band either side of a corridor. */
const STREET = 22;

/**
 * The surround grid is rotated off-axis, and that is not decoration.
 *
 * Axis-aligned, it read as graph paper the moment it met the mapped streets: real road
 * networks almost never run true north, so a perfectly square lattice butting onto
 * Lahore's actual geometry announced exactly where the data stopped. Turned a little, the
 * two networks merely disagree the way two districts of any city disagree.
 */
const SURROUND_ANGLE = 0.34;
const COS_A = Math.cos(SURROUND_ANGLE);
const SIN_A = Math.sin(SURROUND_ANGLE);

/** Into the surround grid's own frame. */
function toGridFrame(x: number, z: number): [number, number] {
  return [x * COS_A - z * SIN_A, x * SIN_A + z * COS_A];
}

/** True on a procedural street corridor — used to carve blocks and to draw the tarmac. */
function onSurroundStreet(x: number, z: number): boolean {
  const [u, v] = toGridFrame(x, z);
  const gapU = ((u % BLOCK) + BLOCK) % BLOCK;
  const gapV = ((v % BLOCK) + BLOCK) % BLOCK;
  return gapU < STREET || gapV < STREET;
}

/**
 * Occupancy cell size, metres.
 *
 * 10 m, not 20. The grid decides where a procedural building may *not* go, and at a
 * 20 m cell the smallest exclusion anything could claim was a 60 m square — so a single
 * mapped 30 m footprint punched a hole four times its own size out of the block around
 * it, and the district came out full of gaps a real city does not have.
 */
export const CELL = 10;
export const GRID = Math.ceil((EXTENT * 2) / CELL);

/** Deterministic hash in 0..1. The city must be the same city on every visit. */
export function hash(x: number, y: number, salt = 0): number {
  const s = Math.sin(x * 127.1 + y * 311.7 + salt * 74.7) * 43758.5453;
  return s - Math.floor(s);
}

const index = (gx: number, gz: number) => gz * GRID + gx;
const toGrid = (metres: number) => Math.floor((metres + EXTENT) / CELL);

export interface Occupancy {
  road: Uint8Array;
  built: Uint8Array;
  green: Uint8Array;
}

function stamp(target: Uint8Array, x: number, z: number, radiusCells: number, value = 1) {
  const gx = toGrid(x);
  const gz = toGrid(z);
  for (let dz = -radiusCells; dz <= radiusCells; dz++) {
    for (let dx = -radiusCells; dx <= radiusCells; dx++) {
      const nx = gx + dx;
      const nz = gz + dz;
      if (nx < 0 || nz < 0 || nx >= GRID || nz >= GRID) continue;
      target[index(nx, nz)] = value;
    }
  }
}

/**
 * The plot an institution actually occupies, metres.
 *
 * OSM maps a campus as its *boundary*, and one of the twenty in this extract is a
 * 1,015 m square — a seventh of the whole district. Drawing it at that size was already
 * rejected (see `buildCampus`), but the occupancy grid was still stamping the raw
 * boundary as built-on, which forbade the infill from placing a single house inside it.
 * The result was a square kilometre of bare ground with one small building marooned in
 * the middle of it: the largest empty area in the scene, and the one that read as
 * missing data rather than as a place.
 *
 * A campus boundary is a *legal* extent, not a built one. Both the drawing and the
 * exclusion now use this, so they can no longer disagree about how much ground an
 * institution takes up.
 */
export function campusFootprint(campus: { w: number; d: number }): { w: number; d: number } {
  return {
    w: Math.max(34, Math.min(108, campus.w)),
    d: Math.max(30, Math.min(92, campus.d)),
  };
}

/**
 * Rasterise a closed ring into the grid, even-odd.
 *
 * Parks arrive as boundaries and were stamped by *walking* them, which marked a band
 * along the edge and left the middle unmarked — so a park was a green ring around a patch
 * of ground the shader painted as bare dirt and the infill was free to build houses on.
 * A scanline fill is the difference between knowing where a park's fence is and knowing
 * where the park is.
 */
function fillRing(target: Uint8Array, points: number[], value = 1) {
  let minZ = Infinity;
  let maxZ = -Infinity;
  for (let i = 1; i < points.length; i += 2) {
    const z = points[i]!;
    if (z < minZ) minZ = z;
    if (z > maxZ) maxZ = z;
  }
  if (!Number.isFinite(minZ)) return;

  const first = Math.max(0, toGrid(minZ));
  const last = Math.min(GRID - 1, toGrid(maxZ));
  const crossings: number[] = [];

  for (let gz = first; gz <= last; gz++) {
    // The centre of this row of cells, back in metres.
    const z = (gz + 0.5) * CELL - EXTENT;
    crossings.length = 0;

    for (let i = 0; i + 3 < points.length; i += 2) {
      const az = points[i + 1]!;
      const bz = points[i + 3]!;
      // Half-open in z, so a vertex exactly on the scanline is counted once rather than
      // twice — the classic way a polygon fill springs a leak along a whole row.
      if (az <= z === bz <= z) continue;
      const ax = points[i]!;
      const bx = points[i + 2]!;
      crossings.push(ax + ((z - az) / (bz - az)) * (bx - ax));
    }

    crossings.sort((a, b) => a - b);
    for (let c = 0; c + 1 < crossings.length; c += 2) {
      const from = Math.max(0, toGrid(crossings[c]!));
      const to = Math.min(GRID - 1, toGrid(crossings[c + 1]!));
      for (let gx = from; gx <= to; gx++) target[index(gx, gz)] = value;
    }
  }
}

/** Walk a polyline at `step` metres, calling back with each sample. */
export function walk(
  points: number[],
  step: number,
  visit: (x: number, z: number, dirX: number, dirZ: number) => void,
) {
  for (let i = 0; i + 3 < points.length; i += 2) {
    const ax = points[i]!;
    const az = points[i + 1]!;
    const bx = points[i + 2]!;
    const bz = points[i + 3]!;
    const length = Math.hypot(bx - ax, bz - az);
    if (length < 1e-3) continue;
    const dirX = (bx - ax) / length;
    const dirZ = (bz - az) / length;
    for (let travelled = 0; travelled < length; travelled += step) {
      visit(ax + dirX * travelled, az + dirZ * travelled, dirX, dirZ);
    }
  }
}

export function buildOccupancy(data: LahoreExtract): Occupancy {
  const road = new Uint8Array(GRID * GRID);
  const built = new Uint8Array(GRID * GRID);
  const green = new Uint8Array(GRID * GRID);

  for (const line of data.roads) {
    // Wider classes claim more ground, which is what keeps buildings off a dual
    // carriageway without any explicit carriageway width in the data. Roughly a 50 m
    // corridor for a trunk road and 30 m for a lane, which is about right.
    const radius = line.c && line.c >= 4 ? 2 : 1;
    walk(line.p, CELL / 2, (x, z) => stamp(road, x, z, radius));
  }
  for (const line of data.water) walk(line.p, CELL / 2, (x, z) => stamp(road, x, z, 3));
  for (const line of data.water) walk(line.p, CELL / 2, (x, z) => stamp(green, x, z, 4));
  // Filled, not walked. The band along the fence is still wanted — a park's planting is
  // densest at its edge — but the middle has to be park too.
  for (const area of data.green) {
    walk(area.p, CELL / 2, (x, z) => stamp(green, x, z, 2));
    fillRing(green, area.p);
  }
  // Sports grounds are green whatever the landuse said, and four of them here are not
  // inside any mapped park.
  for (const pitch of data.pitches ?? []) {
    stamp(green, pitch.x, pitch.z, Math.floor(Math.max(pitch.w, pitch.d) / CELL / 2));
  }
  for (const box of data.buildings) {
    // The footprint and nothing more. Rounding up here is what turned every mapped
    // building into a clearing.
    stamp(built, box.x, box.z, Math.floor(Math.max(box.w, box.d) / CELL / 2));
  }
  for (const campus of data.civic ?? []) {
    const plot = campusFootprint(campus);
    stamp(built, campus.x, campus.z, Math.floor(Math.max(plot.w, plot.d) / CELL / 2));
  }

  return { road, built, green };
}

function sample(layer: Uint8Array, x: number, z: number): number {
  const gx = toGrid(x);
  const gz = toGrid(z);
  if (gx < 0 || gz < 0 || gx >= GRID || gz >= GRID) return 0;
  return layer[index(gx, gz)]!;
}

/** Fraction of a neighbourhood that is built. Drives both infill density and heat. */
function density(layer: Uint8Array, x: number, z: number, radiusCells: number): number {
  const gx = toGrid(x);
  const gz = toGrid(z);
  let hits = 0;
  let total = 0;
  for (let dz = -radiusCells; dz <= radiusCells; dz++) {
    for (let dx = -radiusCells; dx <= radiusCells; dx++) {
      const nx = gx + dx;
      const nz = gz + dz;
      if (nx < 0 || nz < 0 || nx >= GRID || nz >= GRID) continue;
      total++;
      hits += layer[index(nx, nz)]!;
    }
  }
  return total ? hits / total : 0;
}

/**
 * Real footprints plus procedural infill, as one list.
 *
 * The infill is placed on a 34 m lattice and only where the extract maps nothing: not on
 * a road, not on the canal, not inside a park, and not on top of a real building. What
 * comes out is the block structure the street network already implies, which is why it
 * reads as a city rather than as scattered boxes.
 */
export function assembleBuildings(data: LahoreExtract, occupancy: Occupancy): BuildingBox[] {
  const all: BuildingBox[] = [...data.buildings];
  // Tight. Central Lahore is dense, and at a wider pitch the blocks came out as scattered
  // pavilions with ground showing between every pair.
  const STEP = 27;

  for (let x = -FILL_RADIUS; x < FILL_RADIUS; x += STEP) {
    for (let z = -FILL_RADIUS; z < FILL_RADIUS; z += STEP) {
      const radius = Math.hypot(x, z);
      if (radius > FILL_RADIUS) continue;

      const inExtract = Math.abs(x) < EXTENT - STEP && Math.abs(z) < EXTENT - STEP;
      if (inExtract) {
        // Inside the real data, the extract decides: never build on a mapped road, on
        // the canal, on a park, or on top of a footprint that already exists.
        if (sample(occupancy.road, x, z)) continue;
        if (sample(occupancy.built, x, z)) continue;
        if (sample(occupancy.green, x, z)) continue;
      } else if (onSurroundStreet(x, z)) {
        // Beyond it, a plain block grid stands in for the street network. Carving the
        // corridors out of the lattice is what makes the surround read as blocks rather
        // than as an undifferentiated field of boxes.
        continue;
      }

      const jitterX = x + (hash(x, z, 1) - 0.5) * 14;
      const jitterZ = z + (hash(x, z, 2) - 0.5) * 14;

      // Density tapers with distance so the city thins toward the fog instead of ending
      // at a wall. Squared, so the falloff is gentle nearby and quick at the rim.
      const reach = radius / FILL_RADIUS;
      const falloff = 1 - reach * reach;
      // Only the outer third really thins out. Rejecting a tenth of the plots everywhere
      // left gaps through the middle of the district, where a city has none.
      if (hash(x, z, 3) > falloff * 1.12) continue;

      /*
       * Typology, not one generic block.
       *
       * A district is mostly houses, with shop units on the through routes and apartment
       * blocks where the land is worth more. Giving every plot the same size distribution
       * produced a field of interchangeable slabs — the variety here is what makes one
       * street read differently from the next, and it is also what fills the frame,
       * because a house occupies a plot a slab would have left half empty.
       */
      const onThrough = sample(occupancy.road, x + STEP, z) || sample(occupancy.road, x, z + STEP);
      const roll = hash(x, z, 12);

      let plot: number;
      let storeys: number;
      if (onThrough && roll < 0.5) {
        // Shop-houses: narrow frontage, deep plot, two or three floors above the unit.
        plot = 13 + hash(x, z, 4) * 9;
        storeys = 2 + Math.round(hash(x, z, 8) * 2);
      } else if (roll < 0.34 + falloff * 0.25) {
        // Apartment blocks, taller toward the middle of the district.
        plot = 24 + hash(x, z, 4) * 20;
        storeys = Math.max(3, Math.round(4 + hash(x, z, 8) * 3 + falloff * 5));
      } else {
        // Houses. The great majority, one or two storeys, with a yard beside them.
        plot = 15 + hash(x, z, 4) * 11;
        storeys = 1 + Math.round(hash(x, z, 8) * 1.4);
      }
      const height = storeys * 3.2;

      const box: BuildingBox = {
        x: jitterX,
        z: jitterZ,
        w: plot,
        d: plot * (0.6 + hash(x, z, 5) * 0.7),
        // Snapped to the street grid's dominant angle rather than free — buildings in a
        // block face the road, and random yaw is the tell of procedural filler.
        r: Math.round(hash(x, z, 6) * 4) * (Math.PI / 4) + (hash(x, z, 7) - 0.5) * 0.18,
        h: height,
      };
      all.push(box);

      // A setback storey on the taller plots. One extra box on a minority of buildings
      // breaks the skyline out of a field of flat-topped slabs, which is most of what
      // makes a box read as a building rather than as a block.
      if (storeys >= 7 && hash(x, z, 9) > 0.62) {
        all.push({
          x: jitterX,
          z: jitterZ,
          w: box.w * 0.58,
          d: box.d * 0.58,
          r: box.r,
          h: height + Math.round(1 + hash(x, z, 10) * 4) * 3.2,
        });
      }
    }
  }

  return all;
}

export interface RoofUnit {
  x: number;
  z: number;
  /** Base of the unit — the roof it stands on. */
  y: number;
  size: number;
  r: number;
}

/**
 * Rooftop water tanks.
 *
 * Not decoration for its own sake: in Lahore almost every roof carries one, and at this
 * camera distance the silhouette of a roofline is most of what the eye uses to decide
 * whether it is looking at a city or at a bar chart. A few thousand small boxes standing
 * proud of the parapets breaks every roof plane in the district.
 */
export function roofUnits(boxes: BuildingBox[]): RoofUnit[] {
  const units: RoofUnit[] = [];

  for (const box of boxes) {
    // Only on roofs with room for one, and only on some of them.
    if (Math.min(box.w, box.d) < 13) continue;
    if (hash(box.x, box.z, 51) > 0.62) continue;

    const inset = Math.min(box.w, box.d) * 0.22;
    units.push({
      x: box.x + (hash(box.x, box.z, 52) - 0.5) * inset,
      z: box.z + (hash(box.x, box.z, 53) - 0.5) * inset,
      y: box.h,
      size: 1.9 + hash(box.x, box.z, 54) * 1.6,
      r: box.r,
    });
  }

  return units;
}

/**
 * Dashed centre markings down the larger roads.
 *
 * Only on class 3 and above. Marking every residential lane would turn the street network
 * into a bright mesh and undo the work of dimming the tarmac in the first place.
 */
export function centreLines(lines: Polyline[]): Float32Array {
  const positions: number[] = [];
  const DASH = 9;
  const GAP = 11;
  const WIDTH = 0.5;

  for (const line of lines) {
    if ((line.c ?? 1) < 3) continue;

    let travelled = 0;
    for (let i = 0; i + 3 < line.p.length; i += 2) {
      const ax = line.p[i]!;
      const az = line.p[i + 1]!;
      const bx = line.p[i + 2]!;
      const bz = line.p[i + 3]!;
      const length = Math.hypot(bx - ax, bz - az);
      if (length < 1e-3) continue;
      const dirX = (bx - ax) / length;
      const dirZ = (bz - az) / length;
      const nx = -dirZ * WIDTH;
      const nz = dirX * WIDTH;

      for (let at = -(travelled % (DASH + GAP)); at < length; at += DASH + GAP) {
        const start = Math.max(0, at);
        const end = Math.min(length, at + DASH);
        if (end <= start) continue;
        const sx = ax + dirX * start;
        const sz = az + dirZ * start;
        const ex = ax + dirX * end;
        const ez = az + dirZ * end;
        positions.push(
          sx - nx, 0, sz - nz,
          sx + nx, 0, sz + nz,
          ex + nx, 0, ez + nz,
          sx - nx, 0, sz - nz,
          ex + nx, 0, ez + nz,
          ex - nx, 0, ez - nz,
        );
      }
      travelled += length;
    }
  }

  return new Float32Array(positions);
}

/** The procedural street corridors, as polylines, so the surround has visible tarmac. */
export function surroundStreets(): Polyline[] {
  const lines: Polyline[] = [];
  const limit = Math.ceil(FILL_RADIUS / BLOCK) * BLOCK;

  // Back out of the grid frame. Rotation preserves the disc, so a chord computed in grid
  // space is still a chord after the endpoints are turned.
  const fromGridFrame = (u: number, v: number): [number, number] => [
    u * COS_A + v * SIN_A,
    -u * SIN_A + v * COS_A,
  ];

  for (let at = -limit; at <= limit; at += BLOCK) {
    const half = Math.sqrt(Math.max(0, FILL_RADIUS * FILL_RADIUS - at * at));
    if (half < BLOCK) continue;

    const [ax, az] = fromGridFrame(-half, at);
    const [bx, bz] = fromGridFrame(half, at);
    lines.push({ c: 1, p: [ax, az, bx, bz] });

    const [cx, cz] = fromGridFrame(at, -half);
    const [dx, dz] = fromGridFrame(at, half);
    lines.push({ c: 1, p: [cx, cz, dx, dz] });
  }

  return lines;
}

/**
 * How hot a point reads, 0 to 1, for the HEAT ramp.
 *
 * Built-up density warms, parks and the canal cool. It is a plausible surface, not a
 * modelled one — the actual thermal core runs on the server against real Landsat, and
 * nothing in the hero pretends otherwise.
 */
export function heatAt(occupancy: Occupancy, x: number, z: number): number {
  // Outside the extract there is no occupancy to read, and the grid would answer zero for
  // every query — painting the whole surround at the cold end of the ramp, which is both
  // wrong and conspicuous. Out there the value comes from distance and a hash instead.
  if (Math.abs(x) >= EXTENT || Math.abs(z) >= EXTENT) {
    const reach = Math.min(1, Math.hypot(x, z) / FILL_RADIUS);
    return Math.max(0.1, Math.min(0.8, 0.62 - reach * 0.22 + (hash(x, z, 31) - 0.5) * 0.22));
  }

  const built = density(occupancy.built, x, z, 10);
  const cool = density(occupancy.green, x, z, 12);
  // Capped below the ramp's last stop. Letting dense blocks saturate at pure #961a1e
  // turned whole quarters into flat red slabs with no legible structure — the ramp's
  // useful range is its middle, and the top stop should be rare enough to mean something.
  return Math.max(0.06, Math.min(0.88, 0.26 + built * 1.05 - cool * 0.85));
}

export interface TreeSpot {
  x: number;
  z: number;
  scale: number;
  /** True for trees the intervention plants — the canal corridor. */
  planted: boolean;
}

/**
 * Where the trees go.
 *
 * Existing greenery sits in the parks and along the roads; the canal corridor is left
 * bare and filled only as the intervention lands. That is the gesture the hero performs,
 * and it is the same corridor the worked example in the docs plants.
 */
export function placeTrees(data: LahoreExtract, occupancy?: Occupancy): TreeSpot[] {
  const spots: TreeSpot[] = [];

  for (const area of data.green) {
    walk(area.p, 26, (x, z) => {
      if (hash(x, z, 11) > 0.72) return;
      spots.push({ x, z, scale: 0.75 + hash(x, z, 12) * 0.5, planted: false });
    });
  }

  /*
   * The inside of the parks, not only their edges.
   *
   * Walking the ring plants a hedge around an empty field. Now that the green layer is
   * filled rather than outlined, every third cell of it is a candidate — which is what
   * turns the mapped open space from a flat green shape into somewhere with trees in it.
   * Thinned to about half, and jittered off the lattice, because a park planted on a
   * 30 m grid reads as an orchard.
   */
  if (occupancy) {
    const STRIDE = 3;
    for (let gz = 0; gz < GRID; gz += STRIDE) {
      for (let gx = 0; gx < GRID; gx += STRIDE) {
        const cell = index(gx, gz);
        if (occupancy.road[cell] || occupancy.built[cell]) continue;

        const x = (gx + 0.5) * CELL - EXTENT;
        const z = (gz + 0.5) * CELL - EXTENT;
        const isPark = occupancy.green[cell] > 0;

        /*
         * Two densities, because a park and a vacant lot are not the same kind of green.
         *
         * Park cells plant heavily and at full size. Everything else — the ground that is
         * neither road, nor building, nor park — plants sparsely and small: the scrub,
         * the single tree in a yard, the vegetation that takes over any plot nobody is
         * using. That second pass is what closes the last of the open ground. Grass alone
         * had made the gaps green, but a large unbroken green area reads as a lawn the
         * city was cut out of rather than as the bits of it nobody built on.
         */
        // Share of candidate cells that actually get planted.
        const keep = isPark ? 0.65 : 0.38;
        if (hash(x, z, 41) > keep) continue;

        spots.push({
          x: x + (hash(x, z, 42) - 0.5) * CELL * STRIDE,
          z: z + (hash(x, z, 43) - 0.5) * CELL * STRIDE,
          scale: isPark
            ? 0.7 + hash(x, z, 44) * 0.55
            : 0.34 + hash(x, z, 44) * 0.42,
          planted: false,
        });
      }
    }
  }

  for (const line of data.roads) {
    if ((line.c ?? 1) < 3) continue;
    walk(line.p, 46, (x, z, dirX, dirZ) => {
      if (hash(x, z, 13) > 0.4) return;
      const side = hash(x, z, 14) > 0.5 ? 1 : -1;
      spots.push({
        x: x - dirZ * 13 * side,
        z: z + dirX * 13 * side,
        scale: 0.6 + hash(x, z, 15) * 0.4,
        planted: false,
      });
    });
  }

  // The intervention: both banks of the canal, densely.
  for (const line of data.water) {
    walk(line.p, 13, (x, z, dirX, dirZ) => {
      for (const side of [-1, 1]) {
        const offset = 17 + hash(x, z, 16) * 12;
        spots.push({
          x: x - dirZ * offset * side,
          z: z + dirX * offset * side,
          scale: 0.7 + hash(x, z * side, 17) * 0.5,
          planted: true,
        });
      }
    });
  }

  // Scattered greenery through the surround too. Without it the outer ring is nothing but
  // rooftops, and the boundary of the real extract becomes visible as a line where the
  // trees stop — which is exactly the seam the disc was meant to hide.
  for (let x = -FILL_RADIUS; x < FILL_RADIUS; x += 62) {
    for (let z = -FILL_RADIUS; z < FILL_RADIUS; z += 62) {
      if (Math.abs(x) < EXTENT && Math.abs(z) < EXTENT) continue;
      if (Math.hypot(x, z) > FILL_RADIUS - 40) continue;
      if (hash(x, z, 18) > 0.3) continue;
      spots.push({
        x: x + (hash(x, z, 19) - 0.5) * 40,
        z: z + (hash(x, z, 20) - 0.5) * 40,
        scale: 0.6 + hash(x, z, 21) * 0.5,
        planted: false,
      });
    }
  }

  return spots.filter((spot) => Math.hypot(spot.x, spot.z) < FILL_RADIUS);
}

/**
 * Flat ribbons along every road centreline, as one merged geometry.
 *
 * A quad per segment, widened by class. Deliberately not `THREE.Line`: line width is
 * capped at 1 px on every major platform, so a line-based street network renders as
 * hairlines at any camera distance and the city loses its structure.
 */
export function roadRibbons(lines: Polyline[]): {
  positions: Float32Array;
  shades: Float32Array;
} {
  const positions: number[] = [];
  const shades: number[] = [];

  for (const line of lines) {
    const width = 3 + (line.c ?? 1) * 3.4;
    // Dim. The street network is the scene's structure, not its subject — at the previous
    // brightness the roads read as glowing ribbons and the buildings disappeared between
    // them. Class still separates, over a narrower range.
    const shade = 0.1 + (line.c ?? 1) * 0.05;

    for (let i = 0; i + 3 < line.p.length; i += 2) {
      const ax = line.p[i]!;
      const az = line.p[i + 1]!;
      const bx = line.p[i + 2]!;
      const bz = line.p[i + 3]!;
      const length = Math.hypot(bx - ax, bz - az);
      if (length < 1e-3) continue;

      // Perpendicular, scaled to half width. Segments overlap at the joins, which fills
      // the corners without any mitring arithmetic.
      const nx = (-(bz - az) / length) * (width / 2);
      const nz = ((bx - ax) / length) * (width / 2);

      positions.push(
        ax - nx, 0, az - nz,
        ax + nx, 0, az + nz,
        bx + nx, 0, bz + nz,
        ax - nx, 0, az - nz,
        bx + nx, 0, bz + nz,
        bx - nx, 0, bz - nz,
      );
      for (let vertex = 0; vertex < 6; vertex++) shades.push(shade);
    }
  }

  return { positions: new Float32Array(positions), shades: new Float32Array(shades) };
}
