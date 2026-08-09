/**
 * The hero: a real district of Lahore, planted on scroll.
 *
 * The street network, the canal, the parks and 736 building footprints are genuine
 * OpenStreetMap geometry for a 2.8 km square around 31.5225 N, 74.35 E — baked to a
 * static asset by `scripts/bake-lahore.mjs`, so the page opens with no network call. The
 * blocks between the mapped roads are filled in procedurally; see `lahore.ts`.
 *
 * Buildings are coloured by the HEAT ramp from `raster/ramp.ts`, so the hero and the map
 * share one colour system. The surface it colours is plausible, not modelled — built-up
 * density warms, parks and water cool — and the copy never calls it a prediction. The
 * real thermal core runs on the server against observed Landsat.
 *
 * Scroll and the canal corridor plants: trees rise along both banks and the buildings
 * near them shift toward the ramp's blue end. That is the product's one gesture.
 *
 * The district is not only buildings and roads. `cityProps.ts` places fifteen further
 * kinds of object — mosques, streetlights, traffic, walls, billboards, market awnings,
 * cricket grounds, rail, pylons, bridges and the rest — using OSM's real positions
 * wherever it has them and the mapped street network to derive the rest.
 *
 * Everything still draws in about ten instanced calls, because those fifteen object types
 * resolve to three primitives. Per-frame work is the traffic, plus a throttled write to
 * the building colours while the scroll is actually moving.
 */

import { Canvas, useFrame, useLoader, useThree } from "@react-three/fiber";
import { Bloom, EffectComposer, N8AO } from "@react-three/postprocessing";
import type { MotionValue } from "motion/react";
import { Suspense, useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";

import { HEAT } from "../../raster/ramp";
import CityLayer from "./CityLayer";
import { makeFacadeMaterial, makeGroundMaterial } from "./cityMaterial";
import { buildProps } from "./cityProps";
import {
  type BuildingBox,
  FILL_RADIUS,
  type LahoreExtract,
  type Occupancy,
  assembleBuildings,
  buildOccupancy,
  centreLines,
  hash,
  heatAt,
  placeTrees,
  roadRibbons,
  roofUnits,
  surroundStreets,
} from "./lahore";

/** How far a building has to be from the canal to be untouched by the planting. */
const COOLING_REACH_M = 190;

/**
 * Road and marking colours are scaled by this before the sun reaches them.
 *
 * The tarmac shades in `roadRibbons` were picked against an *unlit* basic material, where
 * the number written is the number seen. They are Lambert now, so that they take the
 * building shadows — a shadow that stops dead at the kerb is worse than no shadow at all —
 * and Lambert multiplies them by the scene's whole irradiance, which is about 2x here.
 *
 * It is set above that, not at it, and deliberately: a shadow needs a lit surface to be
 * seen against. At the original near-black tarmac the sun carried so little of the street
 * that switching shadows on changed nothing anybody could point at — the pass ran and the
 * frame looked identical. Sunlit road sits a stop brighter than it used to, and the
 * shadows are what put it back.
 */
const ROAD_LIT = 0.55;

/**
 * Half-width of the sun's shadow frustum, metres.
 *
 * Sized to what the camera can actually see rather than to `FILL_RADIUS`: the fog
 * saturates well inside the disc, so shadowing all 2.9 km of it would spend most of the
 * shadow map's resolution on geometry nobody can make out. 1200 m across a 2048 map is
 * ~1.2 m per texel, comfortably finer than the narrowest building here.
 */
const SHADOW_EXTENT_M = 1200;

/**
 * What Lahore is actually built out of.
 *
 * Colouring every building straight from the HEAT ramp made the city a rainbow — the
 * ramp's job is to encode a measured quantity across a raster, and a whole skyline in
 * primary colours reads as abstraction, not as a place. These are the real materials:
 * rendered concrete, brick, whitewash, and the grey of a flat concrete roof. The thermal
 * signal is then mixed *over* them, so the district looks like a city with a warm quarter
 * rather than like a bar chart standing up.
 */
const MATERIALS = [
  [0.62, 0.58, 0.52], // rendered concrete
  [0.55, 0.41, 0.33], // brick
  [0.72, 0.69, 0.63], // whitewash
  [0.44, 0.42, 0.40], // weathered grey
  [0.66, 0.60, 0.49], // sandstone
];

/** How much of the thermal reading shows through the material. */
const HEAT_MIX = 0.42;

/**
 * A unit box whose vertex colours carry the shading a flat material cannot.
 *
 * Two effects, both baked into the geometry once and free at draw time, because three
 * multiplies the geometry's vertex colour and the per-instance colour together:
 *
 *  - **Ambient occlusion.** Walls darken toward the ground, the way a real street does.
 *    Without it a box is a flat silhouette and the eye reads it as a coloured rectangle.
 *  - **Roofs.** The top face is greyer and flatter than the walls, which is the single
 *    biggest cue that an extruded rectangle is a building seen from above.
 */
function buildingGeometry(): THREE.BufferGeometry {
  const geometry = new THREE.BoxGeometry(1, 1, 1);
  geometry.translate(0, 0.5, 0);

  const position = geometry.attributes.position!;
  const normal = geometry.attributes.normal!;
  const colours = new Float32Array(position.count * 3);

  for (let i = 0; i < position.count; i++) {
    const y = position.getY(i);
    const isRoof = normal.getY(i) > 0.5;
    // 0 at the base, 1 at the parapet.
    const up = Math.max(0, Math.min(1, y));
    let shade = 0.52 + up * 0.48;
    if (isRoof) shade *= 0.82;

    colours[i * 3] = shade * (isRoof ? 0.94 : 1);
    colours[i * 3 + 1] = shade * (isRoof ? 0.95 : 1);
    colours[i * 3 + 2] = shade * (isRoof ? 1.02 : 1);
  }

  geometry.setAttribute("color", new THREE.BufferAttribute(colours, 3));
  return geometry;
}

/** A tiny loader shim: useLoader wants a Loader class, and JSON needs none of one. */
class JsonLoader extends THREE.Loader {
  load(
    url: string,
    onLoad: (data: LahoreExtract) => void,
    _onProgress?: unknown,
    onError?: (error: unknown) => void,
  ) {
    fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`${response.status} loading ${url}`);
        return response.json();
      })
      .then(onLoad)
      .catch(onError);
  }
}

/**
 * Where the sun sits, and what colour the air is.
 *
 * These three are one decision, not three: the fog has to be the colour of the sky at the
 * horizon, or the district dissolves into a haze that does not match the haze behind it
 * and the join shows as a band. `HAZE_HEX` is `HORIZON` converted to sRGB, because a
 * material colour is sRGB and a shader writes linear.
 */
const SUN_POSITION: [number, number, number] = [1500, 900, -700];
/** Linear. Warm dust — this is the atmosphere the tile's PM2.5 is suspended in. */
const HORIZON: [number, number, number] = [0.058, 0.050, 0.039];
const ZENITH: [number, number, number] = [0.017, 0.026, 0.048];
const HAZE_HEX = "#463f39";

const SKY_VERTEX = /* glsl */ `
  varying vec3 vSkyDir;
  void main() {
    // The sphere is centred on the origin, so a local position *is* a direction.
    vSkyDir = normalize(position);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const SKY_FRAGMENT = /* glsl */ `
  varying vec3 vSkyDir;
  uniform vec3 uHorizon;
  uniform vec3 uZenith;
  uniform vec3 uSunDir;

  void main() {
    // Compressed toward the horizon rather than linear in height: almost all of a real
    // sky's colour change happens in the first few degrees above the skyline, and a
    // straight lerp puts the interesting part somewhere nobody is looking.
    float lift = smoothstep(-0.02, 0.42, vSkyDir.y);
    vec3 colour = mix(uHorizon, uZenith, lift * lift);

    // The sun's own glow. Two lobes: a tight disc and the wide forward-scatter that dust
    // gives it, which over Lahore is most of what the sky near the sun actually is.
    float toSun = max(0.0, dot(vSkyDir, uSunDir));
    colour += vec3(0.36, 0.28, 0.17) * pow(toSun, 220.0);
    colour += vec3(0.13, 0.10, 0.065) * pow(toSun, 6.0) * smoothstep(-0.1, 0.3, vSkyDir.y);

    gl_FragColor = vec4(colour, 1.0);
  }
`;

/**
 * The sky, as a shader on the inside of a very large sphere.
 *
 * The hero used to end at a black band across the top third of the frame — the fog was
 * the page's own `#05070a`, so the district faded into the void and the void was most of
 * what sat above the skyline. A city with nothing over it reads as a model on a table.
 *
 * A gradient sphere rather than a real atmospheric scattering model: this is one fixed
 * hour of one fixed morning, so the two parameters that a scattering model would earn its
 * cost by varying are both constants here.
 */
function Sky() {
  const uniforms = useMemo(
    () => ({
      uHorizon: { value: new THREE.Vector3(...HORIZON) },
      uZenith: { value: new THREE.Vector3(...ZENITH) },
      uSunDir: { value: new THREE.Vector3(...SUN_POSITION).normalize() },
    }),
    [],
  );

  return (
    // Inside the camera's far plane (12,000) and well outside the fog, so it is always
    // the backdrop and never something the city can intersect.
    <mesh renderOrder={-1}>
      <sphereGeometry args={[9000, 32, 16]} />
      <shaderMaterial
        uniforms={uniforms}
        vertexShader={SKY_VERTEX}
        fragmentShader={SKY_FRAGMENT}
        side={THREE.BackSide}
        // Never fogged — it *is* what the fog fades into — and never occluding.
        fog={false}
        depthWrite={false}
      />
    </mesh>
  );
}

function Ground({ occupancy }: { occupancy: Occupancy }) {
  const material = useMemo(() => makeGroundMaterial(occupancy), [occupancy]);

  return (
    // A disc, not a plane: the scene rotates, and a square ground would swing its corners
    // through the frame. Sized past the fog so its rim is never reached.
    //
    // 128 segments rather than 72: the ground is textured now, and at 72 the disc's own
    // straight chords were visible as facets against the fog where it used to be one flat
    // colour that hid them.
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.6, 0]} material={material} receiveShadow>
      <circleGeometry args={[FILL_RADIUS * 1.35, 128]} />
    </mesh>
  );
}

function Roads({ data }: { data: LahoreExtract }) {
  const geometry = useMemo(() => {
    // The mapped network plus the procedural grid that carries the surround. Drawn as one
    // mesh so the real streets and the invented ones are the same tarmac.
    const { positions, shades } = roadRibbons([...data.roads, ...surroundStreets()]);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    // One scalar per vertex, expanded to a colour in the material — cheaper to build than
    // three floats, and road class is the only thing that varies.
    const colours = new Float32Array(shades.length * 3);
    for (let i = 0; i < shades.length; i++) {
      const shade = shades[i]!;
      // Faintly warm, not blue-white: tarmac at mid-morning, and it keeps the cool end of
      // the building ramp as the only cold thing in the frame.
      colours[i * 3] = shade * 1.1 * ROAD_LIT;
      colours[i * 3 + 1] = shade * 1.02 * ROAD_LIT;
      colours[i * 3 + 2] = shade * 0.92 * ROAD_LIT;
    }
    geo.setAttribute("color", new THREE.BufferAttribute(colours, 3));
    return geo;
  }, [data]);

  const markings = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(centreLines(data.roads), 3));
    return geo;
  }, [data]);

  return (
    <>
      <mesh geometry={geometry} position={[0, 0.4, 0]} receiveShadow>
        <meshLambertMaterial vertexColors transparent opacity={0.85} />
      </mesh>
      {/* Just above the tarmac, so it never z-fights with it. Lambert too, or the lane
          markings stay lit at full strength straight through a building's shadow. */}
      <mesh geometry={markings} position={[0, 0.55, 0]} receiveShadow>
        <meshLambertMaterial color="#2d2b27" transparent opacity={0.5} />
      </mesh>
    </>
  );
}

function Water({ data }: { data: LahoreExtract }) {
  const geometry = useMemo(() => {
    // The canal, drawn the same way as a road but wider and in its own colour.
    const { positions } = roadRibbons(data.water.map((line) => ({ ...line, c: 7 })));
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    return geo;
  }, [data]);

  return (
    <mesh geometry={geometry} position={[0, 0.6, 0]}>
      <meshBasicMaterial color="#123a52" transparent opacity={0.9} />
    </mesh>
  );
}

function Buildings({
  data,
  occupancy,
  plant,
  animate,
}: {
  data: LahoreExtract;
  occupancy: Occupancy;
  plant: MotionValue<number>;
  animate: boolean;
}) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const applied = useRef(-1);

  const geometry = useMemo(buildingGeometry, []);
  const material = useMemo(makeFacadeMaterial, []);

  const { boxes, baseHeat, coolable, materialOf } = useMemo(() => {
    const list = assembleBuildings(data, occupancy);

    const heat = new Float32Array(list.length);
    const reach = new Float32Array(list.length);
    const material = new Uint8Array(list.length);

    for (let i = 0; i < list.length; i++) {
      const box = list[i]!;
      heat[i] = heatAt(occupancy, box.x, box.z);
      material[i] = Math.floor(hash(box.x, box.z, 41) * MATERIALS.length) % MATERIALS.length;
      // How much of the planting this building feels: everything within the corridor,
      // tapering to nothing beyond it. Cooling that reached the whole tile would
      // contradict the one thing the map is careful about.
      let nearest = Infinity;
      for (const line of data.water) {
        for (let p = 0; p + 1 < line.p.length; p += 2) {
          const distance = Math.hypot(box.x - line.p[p]!, box.z - line.p[p + 1]!);
          if (distance < nearest) nearest = distance;
        }
      }
      reach[i] = Math.max(0, 1 - nearest / COOLING_REACH_M);
    }

    return { boxes: list, baseHeat: heat, coolable: reach, materialOf: material };
  }, [data, occupancy]);

  // The props hang off the finished building list — dishes and stair heads sit on real
  // roofs — so they are derived here rather than assembled a second time.
  const props = useMemo(
    () => buildProps(data, occupancy, boxes),
    [data, occupancy, boxes],
  );

  // Positions never change, so the matrices are written once at mount. This has to be an
  // effect rather than a memo: a memo runs during render, before the ref is attached, and
  // would silently leave every instance on the identity matrix — one box at the origin.
  useLayoutEffect(() => {
    const node = mesh.current;
    if (!node) return;
    const matrix = new THREE.Matrix4();
    const quaternion = new THREE.Quaternion();
    const euler = new THREE.Euler();
    for (let i = 0; i < boxes.length; i++) {
      const box = boxes[i]!;
      euler.set(0, -box.r, 0);
      quaternion.setFromEuler(euler);
      matrix.compose(
        new THREE.Vector3(box.x, box.h / 2, box.z),
        quaternion,
        new THREE.Vector3(box.w, box.h, box.d),
      );
      node.setMatrixAt(i, matrix);
    }
    node.instanceMatrix.needsUpdate = true;
  }, [boxes]);

  useFrame(() => {
    const node = mesh.current;
    if (!node) return;
    const progress = plant.get();
    // Rewriting 3,000 colours every frame is wasted work when the scroll is still. A
    // hundredth of the range is well below what the eye resolves in a colour ramp.
    if (Math.abs(progress - applied.current) < 0.01) return;
    applied.current = progress;

    const colour = new THREE.Color();
    for (let i = 0; i < boxes.length; i++) {
      const cooled = baseHeat[i]! - progress * coolable[i]! * 0.42;
      const [hr, hg, hb] = HEAT.at(cooled);
      const material = MATERIALS[materialOf[i]!]!;
      // Material first, thermal signal mixed over it. The other way round — a heat colour
      // tinted by a material — puts the ramp back in charge and the rainbow returns.
      colour.setRGB(
        material[0]! * (1 - HEAT_MIX) + (hr / 255) * HEAT_MIX,
        material[1]! * (1 - HEAT_MIX) + (hg / 255) * HEAT_MIX,
        material[2]! * (1 - HEAT_MIX) + (hb / 255) * HEAT_MIX,
        THREE.SRGBColorSpace,
      );
      node.setColorAt(i, colour);
    }
    if (node.instanceColor) node.instanceColor.needsUpdate = true;
  });

  return (
    <>
      <instancedMesh
        ref={mesh}
        args={[geometry, material, boxes.length]}
        frustumCulled={false}
        castShadow
        receiveShadow
      />
      <RoofUnits boxes={boxes} />
      <CityLayer props={props} data={data} animate={animate} />
    </>
  );
}

/** Water tanks. See `roofUnits` for why a rooftop needs something standing on it. */
function RoofUnits({ boxes }: { boxes: BuildingBox[] }) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const units = useMemo(() => roofUnits(boxes), [boxes]);

  useLayoutEffect(() => {
    const node = mesh.current;
    if (!node) return;
    const matrix = new THREE.Matrix4();
    const quaternion = new THREE.Quaternion();
    const euler = new THREE.Euler();
    for (let i = 0; i < units.length; i++) {
      const unit = units[i]!;
      euler.set(0, -unit.r, 0);
      quaternion.setFromEuler(euler);
      matrix.compose(
        new THREE.Vector3(unit.x, unit.y + unit.size * 0.5, unit.z),
        quaternion,
        new THREE.Vector3(unit.size, unit.size, unit.size * 0.75),
      );
      node.setMatrixAt(i, matrix);
    }
    node.instanceMatrix.needsUpdate = true;
  }, [units]);

  return (
    <instancedMesh
      ref={mesh}
      args={[null!, null!, units.length]}
      frustumCulled={false}
      castShadow
    >
      <boxGeometry args={[1, 1, 1]} />
      <meshLambertMaterial color="#4a4640" />
    </instancedMesh>
  );
}

function Trees({
  data,
  occupancy,
  plant,
}: {
  data: LahoreExtract;
  occupancy: Occupancy;
  plant: MotionValue<number>;
}) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const trunks = useRef<THREE.InstancedMesh>(null);
  const applied = useRef(-1);

  const spots = useMemo(() => placeTrees(data, occupancy), [data, occupancy]);

  // Foliage is never one green. Varying the crown per tree is the difference between a
  // row of identical lollipops and a street of trees; written once, since it never
  // changes as the planting grows.
  useLayoutEffect(() => {
    const node = mesh.current;
    if (!node) return;
    const colour = new THREE.Color();
    for (let i = 0; i < spots.length; i++) {
      const spot = spots[i]!;
      const tone = hash(spot.x, spot.z, 33);
      colour.setRGB(
        0.13 + tone * 0.1,
        0.3 + tone * 0.18,
        0.16 + tone * 0.08,
        THREE.SRGBColorSpace,
      );
      node.setColorAt(i, colour);
    }
    if (node.instanceColor) node.instanceColor.needsUpdate = true;
  }, [spots]);

  useFrame(() => {
    const node = mesh.current;
    const trunk = trunks.current;
    if (!node || !trunk) return;
    const progress = plant.get();
    if (Math.abs(progress - applied.current) < 0.01) return;
    applied.current = progress;

    const matrix = new THREE.Matrix4();
    const position = new THREE.Vector3();
    const quaternion = new THREE.Quaternion();
    const scale = new THREE.Vector3();

    for (let i = 0; i < spots.length; i++) {
      const spot = spots[i]!;
      // Planted trees grow in over the scroll, staggered so the corridor fills along its
      // length rather than everywhere at once. Existing greenery is always there.
      const stagger = hash(spot.x, spot.z, 21) * 0.45;
      const grown = spot.planted
        ? Math.max(0, Math.min(1, (progress - stagger) / (1 - stagger)))
        : 1;
      const size = spot.scale * grown;

      position.set(spot.x, 8.5 * size, spot.z);
      scale.set(size, size * 0.85, size);
      matrix.compose(position, quaternion, scale);
      node.setMatrixAt(i, matrix);

      position.set(spot.x, 3.5 * size, spot.z);
      scale.set(size, size, size);
      matrix.compose(position, quaternion, scale);
      trunk.setMatrixAt(i, matrix);
    }
    node.instanceMatrix.needsUpdate = true;
    trunk.instanceMatrix.needsUpdate = true;
  });

  return (
    <>
      {/* Canopies cast but do not receive: a crown is a single convex blob here, so
          self-shadowing buys nothing and costs a second pass over several thousand
          instances. The shadow a tree throws down the street is the whole point of it. */}
      <instancedMesh
        ref={mesh}
        args={[null!, null!, spots.length]}
        frustumCulled={false}
        castShadow
      >
        {/* An icosahedron is a canopy at this distance, at a fraction of the vertices of
            a smooth sphere across several thousand instances. Flat shaded, so each facet
            catches the light differently and the crown reads as foliage. */}
        <icosahedronGeometry args={[6, 0]} />
        {/* No `vertexColors`. The per-tree green arrives as an *instance* colour, and an
            icosahedron has no `color` attribute — asking for vertex colours on a geometry
            that has none makes the shader read a missing attribute, which WebGL supplies
            as zero. Every crown multiplied out to black, which is what the canopy was
            drawing before: silhouettes, on a page whose whole subject is planting. */}
        <meshLambertMaterial flatShading />
      </instancedMesh>
      <instancedMesh
        ref={trunks}
        args={[null!, null!, spots.length]}
        frustumCulled={false}
        castShadow
      >
        <cylinderGeometry args={[0.8, 1.1, 7, 5]} />
        <meshLambertMaterial color="#3d3128" />
      </instancedMesh>
    </>
  );
}

/** Where the camera rests, and where the CTA sends it. Metres, scene space. */
const CAMERA_REST: [number, number, number] = [560, 300, 760];
const CAMERA_DIVE: [number, number, number] = [96, 34, 150];

/**
 * The CTA's fly-through: the camera drops out of the oblique aerial and into the streets.
 *
 * Exponential damping rather than a tween, so it is frame-rate independent and leaves at
 * speed before easing out — which is what makes a descent read as a descent rather than
 * as a zoom. `lookAt` holds a point 60 m up in the middle of the district, so the horizon
 * rises through the frame as the camera falls and the buildings close in over it.
 *
 * The route change is the caller's, on a timer, not this component's: a camera move that
 * navigates would fire whether or not anyone clicked anything.
 */
function FlyThrough({ flying }: { flying: boolean }) {
  const { camera } = useThree();
  const target = useMemo(() => new THREE.Vector3(), []);
  const focus = useMemo(() => new THREE.Vector3(0, 60, 0), []);

  useFrame((_, delta) => {
    target.set(...(flying ? CAMERA_DIVE : CAMERA_REST));
    if (camera.position.distanceToSquared(target) < 1) return;

    camera.position.lerp(target, 1 - Math.exp(-1.5 * Math.min(delta, 0.05)));
    camera.lookAt(focus);
  });

  return null;
}

function Scene({
  plant,
  animate,
  flying,
}: {
  plant: MotionValue<number>;
  animate: boolean;
  flying: boolean;
}) {
  const data = useLoader(JsonLoader, "/lahore.json") as unknown as LahoreExtract;
  const group = useRef<THREE.Group>(null);

  // One occupancy grid for the whole scene. It was being rebuilt twice inside `Buildings`
  // and is now also what the ground is painted from, so it belongs to the scene rather
  // than to any one thing that reads it.
  const occupancy = useMemo(() => buildOccupancy(data), [data]);

  useFrame((_, delta) => {
    // A full turn takes about seven minutes: present, never noticed as movement, which is
    // the difference between atmosphere and a carousel.
    if (animate && group.current) group.current.rotation.y += delta * 0.014;
  });

  return (
    <>
      {/* A hemisphere light rather than a flat ambient: warm from above, cool bounce from
          the ground, which separates roofs from walls before the sun does anything.
          The fill is *low* against the sun, and that ratio is what makes the shadows
          readable rather than any shadow setting. At the fill this scene once carried
          (1.9 and 0.55) the sun was only about a third of the light on an up-facing
          surface, so a shadow removed a third of it: present in the buffer, invisible on
          the screen, and easy to mistake for shadows being broken. A shadow is only as
          dark as the light it takes away. */}
      <hemisphereLight args={["#a8bdd6", "#33261c", 0.40]} />
      <ambientLight intensity={0.15} />
      {/* Low morning sun — the Landsat overpass is ~10:30 local, and ~28 degrees of
          elevation is about right for it — but swung round to roughly 75 degrees off the
          camera's own bearing.
          That azimuth is the whole reason shadows are visible here. It sat at (1400, 850,
          700), which is 27 degrees from where the camera stands: the sun was over the
          viewer's shoulder, so every shadow fell directly behind the building that cast it
          and the district rendered exactly as it had before shadows were turned on. The
          pass ran, cost what it costs, and could not be seen. Side-lighting is what puts a
          shadow across a street where somebody is looking at it.
          One shadow camera, not a cascade. A cascade earns its complexity when the view
          runs to a horizon at ground level; this camera is up at 300 m looking down into a
          district that fogs out well before the disc's rim, so a single ortho frustum tight
          around it resolves the whole visible scene. */}
      <directionalLight
        position={SUN_POSITION}
        intensity={3.4}
        color="#ffe9cc"
        castShadow
        shadow-mapSize={[2048, 2048]}
        // In metres, because the scene is. Depth bias alone either leaves acne on the
        // sunlit walls or peels every shadow away from the wall that casts it; offsetting
        // along the normal is what fixes both at this scale.
        shadow-normalBias={1.6}
      >
        {/**
         * The shadow frustum as a *constructed* camera, not as `shadow-camera-left` and
         * friends.
         *
         * Setting those props individually leaves the projection matrix stale — nothing
         * calls `updateProjectionMatrix` for you — so the shadow map keeps the
         * OrthographicCamera default of a 10 m box with a 500 m far plane. The sun here is
         * 1.9 km out, which puts the entire district behind that far plane: the pass runs
         * every frame, costs what it costs, and renders nothing at all. Silent, and
         * indistinguishable from having forgotten `castShadow`.
         *
         * Passing the frustum as constructor args gets a correct projection matrix on the
         * first frame, because the constructor computes one.
         */}
        <orthographicCamera
          attach="shadow-camera"
          args={[
            -SHADOW_EXTENT_M,
            SHADOW_EXTENT_M,
            SHADOW_EXTENT_M,
            -SHADOW_EXTENT_M,
            200,
            4200,
          ]}
        />
      </directionalLight>
      <Sky />
      <group ref={group}>
        <Ground occupancy={occupancy} />
        <Roads data={data} />
        <Water data={data} />
        <Buildings data={data} occupancy={occupancy} plant={plant} animate={animate} />
        <Trees data={data} occupancy={occupancy} plant={plant} />
      </group>
      <FlyThrough flying={flying} />

      {/**
       * Two passes, and only two.
       *
       * `N8AO` is ambient occlusion from the depth buffer alone — no normal pass, which
       * matters because every material here is Lambert and a normal pass would mean a
       * second full render of the district. It is the pass this scene needed most: the
       * blocks are packed tight enough that the gaps between them were the one place the
       * flat lighting showed, and contact darkening is what puts a building on the ground
       * rather than in front of it.
       *
       * `Bloom` is deliberately near the top of the range. This is a mid-morning scene —
       * the naming rule the whole project runs on — so bloom that caught the façades
       * would turn it into dusk. At a 0.86 threshold it reaches the lit window panes, the
       * lamp heads and the sun side of a whitewashed wall, and nothing else.
       *
       * No SMAA: the composer multisamples, which is what the canvas's own `antialias`
       * was doing before it was drawing into a render target.
       */}
      <EffectComposer multisampling={4}>
        <N8AO aoRadius={18} intensity={2.4} distanceFalloff={0.6} halfRes />
        <Bloom intensity={0.5} luminanceThreshold={0.86} luminanceSmoothing={0.3} mipmapBlur />
      </EffectComposer>
    </>
  );
}

export interface LahoreCityProps {
  /** 0 to 1: how far the planting has landed. Driven by the hero's scroll progress. */
  plant: MotionValue<number>;
  /** False under prefers-reduced-motion — the city renders, the slow rotation stops. */
  animate?: boolean;
  /** True once the CTA is pressed: the camera dives into the streets. */
  flying?: boolean;
}

export default function LahoreCity({
  plant,
  animate = true,
  flying = false,
}: LahoreCityProps) {
  return (
    <Canvas
      className="absolute inset-0"
      // PCF-soft shadow maps. The sun is low, so the shadows are long and their softness
      // is most of what stops 2,048 texels from reading as a staircase.
      shadows="soft"
      // Low and close, like an oblique aerial rather than a satellite. The façades carry
      // storey lines and window bays now, and from 2 km up none of that resolves — the
      // detail only pays for itself if the camera is near enough to read it.
      camera={{ position: [560, 300, 760], fov: 40, near: 5, far: 12000 }}
      // Uncapped DPR quadruples the pixels on a retina panel for no visible gain on flat
      // shaded boxes. 1.6 is where the edges stop looking stepped.
      dpr={[1, 1.6]}
      gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
    >
      {/* Fog in the page's own black, so the district dissolves into the background
          instead of ending on a visible edge. */}
      {/* Saturates *before* FILL_RADIUS, not after. At 3400 the rim was still a quarter
          visible and the disc's curve showed as an arc across the skyline; the far plane
          has to be inside the geometry for the city to end in haze instead of at an edge. */}
      <fog attach="fog" args={[HAZE_HEX, 1100, 3200]} />
      <Suspense fallback={null}>
        <Scene plant={plant} animate={animate} flying={flying} />
      </Suspense>
    </Canvas>
  );
}
