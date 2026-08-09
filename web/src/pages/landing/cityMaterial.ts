/**
 * The façade shader: what turns an extruded rectangle into a building.
 *
 * Flat-shaded boxes read as blocks no matter how carefully they are coloured, because
 * every cue a real building gives — floor lines, window bays, a parapet, a shopfront at
 * street level — happens *within* the face rather than at its silhouette. This patches
 * those in procedurally in the fragment shader, so nothing is added to the geometry and
 * all several thousand buildings still draw in one call.
 *
 * It is a patch on `MeshLambertMaterial` rather than a material of its own, so instanced
 * colours, vertex colours, fog and the scene's lighting all keep working. Everything is
 * derived from what the instance already carries:
 *
 *  - the box's **real dimensions**, recovered from the length of the instance matrix's
 *    basis vectors, so window bays are a fixed size in metres rather than a fixed
 *    fraction of each building — which would make a tower's windows enormous;
 *  - a **per-building seed**, taken from the instance's own translation, so one building's
 *    lit windows are its own and the pattern does not repeat across the district.
 */

import * as THREE from "three";

import { EXTENT, GRID, type Occupancy } from "./lahore";

/** Floor-to-floor, metres. The same figure the height quantisation uses. */
const STOREY_M = 3.2;
/** Window bay pitch across a façade, metres. */
const BAY_M = 2.9;

const VERTEX_HEAD = /* glsl */ `
  varying vec3 vFacadeLocal;
  varying vec3 vFacadeDims;
  varying float vFacadeSeed;
`;

const VERTEX_BODY = /* glsl */ `
  vFacadeLocal = position;
  // Column lengths of the instance matrix are the box's world dimensions.
  vFacadeDims = vec3(
    length(instanceMatrix[0].xyz),
    length(instanceMatrix[1].xyz),
    length(instanceMatrix[2].xyz)
  );
  // The instance's own position, folded into one number. Stable per building.
  vFacadeSeed = fract(sin(instanceMatrix[3].x * 12.9898 + instanceMatrix[3].z * 78.233) * 43758.5453);
`;

const FRAGMENT_HEAD = /* glsl */ `
  varying vec3 vFacadeLocal;
  varying vec3 vFacadeDims;
  varying float vFacadeSeed;

  float facadeHash(vec2 p, float seed) {
    return fract(sin(dot(p, vec2(127.1, 311.7)) + seed * 53.7) * 43758.5453);
  }
`;

/**
 * Everything that happens inside a face.
 *
 * The silhouette of an extruded rectangle carries almost no information about what kind
 * of building it is — a house, a shop-house and an office tower are the same box at
 * different scales. All of the difference lives in the surface, so all of it is here.
 *
 * The rules are typological rather than decorative. A building over ~24 m is treated as a
 * commercial frame and gets continuous glazing between mullions; anything shorter is
 * masonry with punched windows. Both get a plinth, a cornice, bay pilasters, sills, and
 * the splash-staining that any wall in a monsoon city has at its base. A share of the
 * mid-rise gets balconies, which is the single cue that says *residential*.
 *
 * Everything is derived from `vFacadeSeed`, so a building's details are its own and stay
 * the same on every visit, and everything is measured in **metres** off `vFacadeDims`, so
 * a storey is a storey whether it is on a bungalow or a tower.
 */
const FRAGMENT_BODY = /* glsl */ `
{
  // The unit box is centred in x and z and runs 0..1 in y, so the face a fragment sits on
  // is whichever local coordinate is at its extreme.
  bool isRoof = vFacadeLocal.y > 0.995;
  bool isEndFace = abs(vFacadeLocal.x) > 0.495;

  float height = vFacadeDims.y;

  if (isRoof) {
    // A parapet: the roof's outer band is a little lighter than its deck, which is what
    // gives a rooftop an edge when it is seen from above.
    float edge = max(abs(vFacadeLocal.x), abs(vFacadeLocal.z)) * 2.0;
    diffuseColor.rgb *= mix(0.94, 1.16, smoothstep(0.82, 0.99, edge));
    // Deck grime, so a thousand roofs are not one flat grey.
    float grime = facadeHash(floor(vFacadeLocal.xz * 9.0), vFacadeSeed);
    diffuseColor.rgb *= 0.9 + grime * 0.2;

    // Roof deck in metres, so what stands on it is sized in metres too.
    vec2 deck = vec2(vFacadeLocal.x * vFacadeDims.x, vFacadeLocal.z * vFacadeDims.z);

    // The stair head. Every flat roof reachable from inside has one, it is always in a
    // corner rather than the middle, and it is the thing that stops a roof reading as a
    // lid. Drawn, not modelled — at this distance a darker rectangle with a bright edge
    // is indistinguishable from the box that would cost 3,000 more instances.
    vec2 headPos = vec2(
      (facadeHash(vec2(3.0, 1.0), vFacadeSeed) - 0.5) * max(0.0, vFacadeDims.x - 7.0),
      (facadeHash(vec2(1.0, 3.0), vFacadeSeed) - 0.5) * max(0.0, vFacadeDims.z - 7.0)
    );
    vec2 head = abs(deck - headPos);
    float onHead = step(head.x, 1.8) * step(head.y, 1.5);
    diffuseColor.rgb = mix(diffuseColor.rgb, diffuseColor.rgb * 1.24, onHead);
    // Its shadow side, one step further out — the cheapest possible relief.
    float headShade = step(head.x, 2.4) * step(head.y, 2.1) * (1.0 - onHead);
    diffuseColor.rgb *= 1.0 - headShade * 0.34;

    // Water staining pooled along the deck, which is what a flat roof actually looks
    // like anywhere it rains hard and drains slowly.
    float pooling = facadeHash(floor(deck * 0.34), vFacadeSeed + 5.0);
    diffuseColor.rgb *= 1.0 - smoothstep(0.72, 1.0, pooling) * 0.16;
  } else {
    // Metres along the façade, and metres up it.
    float faceWidth = isEndFace ? vFacadeDims.z : vFacadeDims.x;
    float acrossN = isEndFace ? vFacadeLocal.z : vFacadeLocal.x;
    float across = acrossN * faceWidth;
    float up = vFacadeLocal.y * height;

    float storey = floor(up / ${STOREY_M.toFixed(1)});
    float bay = floor(across / ${BAY_M.toFixed(1)});
    float inStorey = fract(up / ${STOREY_M.toFixed(1)});
    float inBay = fract(across / ${BAY_M.toFixed(1)});

    // Metres in from the nearest vertical corner of this face.
    float toCorner = (0.5 - abs(acrossN)) * faceWidth;

    // Over about eight storeys a building here is a commercial frame rather than load-
    // bearing masonry, and the two do not have the same windows.
    bool framed = height > 24.0;

    // The slab edge between floors. One dark line per storey does more for scale than
    // any amount of colour variation: it tells the eye how tall the building is.
    float slab = smoothstep(0.0, 0.06, inStorey) * smoothstep(0.14, 0.07, inStorey);
    diffuseColor.rgb *= 1.0 - slab * 0.42;

    // Pilasters: the structural line between bays. Faint, but it is what stops a long
    // wall of identical windows from reading as wallpaper.
    float pilaster = smoothstep(0.10, 0.0, inBay) + smoothstep(0.90, 1.0, inBay);
    diffuseColor.rgb *= 1.0 + pilaster * 0.055;

    // The corner returns, darker than the face they turn from. This is the one detail
    // that gives a box a readable edge once the sun is off it.
    diffuseColor.rgb *= 1.0 - smoothstep(0.55, 0.0, toCorner) * 0.22;

    // ---- the ground floor -------------------------------------------------------
    // A plinth on everything: the base course is always a different material from the
    // wall above it, and always dirtier.
    float plinth = smoothstep(1.15, 0.85, up);
    diffuseColor.rgb = mix(diffuseColor.rgb, diffuseColor.rgb * 0.72, plinth);

    // Shop units, on some buildings of two storeys and up. Roller shutters, ribbed, with
    // a fascia board over them — the ground floor of most through-streets in Lahore.
    float hasShop = step(0.42, facadeHash(vec2(7.0, 3.0), vFacadeSeed)) * step(6.0, height);
    if (hasShop > 0.5 && up > 0.55 && up < 2.85) {
      float unit = fract(across / 3.6);
      float rib = fract(across / 0.24);
      // Mullion between two units, then the ribbing of the shutter itself.
      float mullion = smoothstep(0.06, 0.0, unit) + smoothstep(0.94, 1.0, unit);
      vec3 shutter = vec3(0.30, 0.29, 0.28) * (0.86 + 0.28 * step(0.5, rib));
      diffuseColor.rgb = mix(shutter, diffuseColor.rgb, max(mullion, 0.18));
    }
    // The fascia over the shopfront, catching the sun.
    diffuseColor.rgb *= 1.0 + hasShop * smoothstep(2.85, 3.05, up) * smoothstep(3.35, 3.15, up) * 0.5;

    // ---- windows ----------------------------------------------------------------
    float pane = 0.0;
    float glazing = 0.82;

    if (framed) {
      // A curtain wall: glazing runs the full bay and is interrupted only by mullions
      // and the spandrel panel at each floor.
      pane =
        step(abs(inBay - 0.5), 0.38) *
        step(abs(inStorey - 0.62), 0.26) *
        step(1.0, up / ${STOREY_M.toFixed(1)});
      glazing = 0.9;
    } else {
      // Punched openings in masonry: smaller, squarer, with wall between them.
      pane =
        step(abs(inBay - 0.5), 0.30) *
        step(abs(inStorey - 0.58), 0.19) *
        // Ground floor stays solid — a window at pavement level looks wrong under a
        // shopfront, and a compound wall is what is usually there instead.
        step(1.0, up / ${STOREY_M.toFixed(1)});
    }

    if (pane > 0.5) {
      // Roughly one pane in twenty, and only warm enough to catch the eye. At one in
      // eight, with a bright fill, the district read as a city at night — and this is a
      // mid-morning scene, which is the whole naming rule the project runs on.
      float lit = step(0.955, facadeHash(vec2(bay, storey), vFacadeSeed));
      // Glass is not one colour: it takes the sky at a different angle on every floor.
      float tint = facadeHash(vec2(bay, storey) + 11.0, vFacadeSeed);
      vec3 glass = mix(vec3(0.085, 0.108, 0.145), vec3(0.135, 0.150, 0.172), tint);
      glass = mix(glass, vec3(0.54, 0.47, 0.34), lit);
      diffuseColor.rgb = mix(diffuseColor.rgb, glass, glazing);

      // A glazing bar across the opening, so a pane is a window rather than a hole.
      float bar = smoothstep(0.035, 0.0, abs(inBay - 0.5));
      diffuseColor.rgb *= 1.0 + bar * 0.7;
    } else {
      // Sill under the opening, lintel over it. Both catch the light, and together they
      // are most of what makes a window look set *into* a wall.
      float sill = step(abs(inBay - 0.5), 0.34) *
                   smoothstep(0.40, 0.36, inStorey) * smoothstep(0.31, 0.35, inStorey);
      float lintel = step(abs(inBay - 0.5), 0.33) *
                     smoothstep(0.78, 0.80, inStorey) * smoothstep(0.86, 0.83, inStorey);
      diffuseColor.rgb *= 1.0 + (sill + lintel) * 0.30;

      // A split unit under a share of the windows. Ubiquitous, and it breaks up the
      // dead wall between openings better than any amount of noise.
      float hasAc = step(0.72, facadeHash(vec2(bay, storey) + 3.0, vFacadeSeed));
      float ac = hasAc *
                 step(abs(inBay - 0.72), 0.13) *
                 step(abs(inStorey - 0.30), 0.075) *
                 step(1.0, up / ${STOREY_M.toFixed(1)});
      diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.34, 0.34, 0.33), ac);
    }

    // ---- balconies --------------------------------------------------------------
    // On a share of the mid-rise only. A balcony on a tower is a different building and
    // a balcony on a bungalow is a veranda; neither is what this draws.
    float hasBalcony = step(0.55, facadeHash(vec2(2.0, 9.0), vFacadeSeed)) *
                       step(9.0, height) * (1.0 - step(24.0, height));
    if (hasBalcony > 0.5) {
      float onBay = step(0.45, facadeHash(vec2(bay, storey) + 21.0, vFacadeSeed));
      float slabLine = smoothstep(0.20, 0.16, inStorey) * smoothstep(0.10, 0.14, inStorey);
      float rail = smoothstep(0.34, 0.30, inStorey) * smoothstep(0.22, 0.26, inStorey);
      float within = step(abs(inBay - 0.5), 0.40) * onBay * step(1.0, up / ${STOREY_M.toFixed(1)});
      // The recess reads as shadow, the slab nosing and the rail as the bright edges
      // above and below it.
      diffuseColor.rgb *= 1.0 - within * (1.0 - slabLine) * (1.0 - rail) * 0.30;
      diffuseColor.rgb *= 1.0 + within * (slabLine * 0.42 + rail * 0.26);
    }

    // ---- top and bottom ---------------------------------------------------------
    // The cornice, right under the parapet. A wall that simply stops is the last thing
    // that gives an extrusion away.
    float cornice = smoothstep(0.85, 0.55, height - up) * smoothstep(0.05, 0.30, height - up);
    diffuseColor.rgb *= 1.0 + cornice * 0.34;
    // And the shadow it casts on the wall below itself.
    diffuseColor.rgb *= 1.0 - smoothstep(1.35, 0.95, height - up) * smoothstep(0.85, 0.95, height - up) * 0.30;

    // Splash staining off the pavement. Every wall in a city with a monsoon has this,
    // and it anchors the building to the ground it stands on.
    diffuseColor.rgb *= 1.0 - smoothstep(1.6, 0.0, up) * (0.10 + facadeHash(vec2(floor(across * 0.5), 0.0), vFacadeSeed) * 0.13);

    // Vertical weathering streaks below the window line, which is what stops a big blank
    // wall from looking like painted card.
    float streak = facadeHash(vec2(floor(across * 0.7), 0.0), vFacadeSeed);
    diffuseColor.rgb *= 1.0 - streak * 0.06;
  }
}
`;

/**
 * Patch a Lambert material to draw façades.
 *
 * `customProgramCacheKey` is set so three does not hand this material the cached program
 * of an unpatched Lambert with the same parameters.
 */
export function makeFacadeMaterial(): THREE.MeshLambertMaterial {
  const material = new THREE.MeshLambertMaterial({ vertexColors: true });

  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", `#include <common>\n${VERTEX_HEAD}`)
      .replace("#include <begin_vertex>", `#include <begin_vertex>\n${VERTEX_BODY}`);

    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>\n${FRAGMENT_HEAD}`)
      // After the instance and vertex colours have been folded in, so the façade detail
      // modulates the building's own material rather than being overwritten by it.
      .replace("#include <color_fragment>", `#include <color_fragment>\n${FRAGMENT_BODY}`);
  };

  material.customProgramCacheKey = () => "terrarium-facade-v2";
  return material;
}

/* ------------------------------------------------------------------ *\
   The ground
\* ------------------------------------------------------------------ */

/**
 * The occupancy grid as a texture the ground shader can read.
 *
 * The same 10 m grid that decides where a procedural building may stand also knows what
 * every square of the district *is* — carriageway, park, institutional plot, or the plain
 * dirt between them. That is exactly what the ground needs in order to stop being one
 * flat colour, and it has already been computed, so this is a repackaging rather than a
 * second pass over the geometry.
 *
 * `NearestFilter`, deliberately. Bilinear would bleed grass a full cell past a park's
 * edge and smear the boundary the trees are planted along; the cell is a unit here, the
 * same way it is in the cube.
 */
function occupancyTexture(occupancy: Occupancy): THREE.DataTexture {
  const data = new Uint8Array(GRID * GRID * 4);
  for (let i = 0; i < GRID * GRID; i++) {
    data[i * 4] = occupancy.road[i] ? 255 : 0;
    data[i * 4 + 1] = occupancy.green[i] ? 255 : 0;
    data[i * 4 + 2] = occupancy.built[i] ? 255 : 0;
    data[i * 4 + 3] = 255;
  }
  const texture = new THREE.DataTexture(data, GRID, GRID, THREE.RGBAFormat);
  texture.minFilter = THREE.NearestFilter;
  texture.magFilter = THREE.NearestFilter;
  texture.needsUpdate = true;
  return texture;
}

const GROUND_VERTEX_HEAD = /* glsl */ `
  varying vec3 vGroundWorld;
`;

const GROUND_VERTEX_BODY = /* glsl */ `
  vGroundWorld = (modelMatrix * vec4(position, 1.0)).xyz;
`;

const GROUND_FRAGMENT_HEAD = /* glsl */ `
  varying vec3 vGroundWorld;
  uniform sampler2D uOccupancy;
  uniform float uExtent;

  float groundHash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
  }

  // Value noise. Two octaves of it are the difference between "ground" and "a grey disc".
  float groundNoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(
      mix(groundHash(i), groundHash(i + vec2(1.0, 0.0)), u.x),
      mix(groundHash(i + vec2(0.0, 1.0)), groundHash(i + vec2(1.0, 1.0)), u.x),
      u.y
    );
  }
`;

/**
 * What the ground is made of.
 *
 * The disc used to be one flat `#31302b`, and over the mapped parks and the institutional
 * plots — where the infill is forbidden from building and so nothing at all stands — that
 * flat colour was most of what the camera saw. A large uniform grey area reads as missing
 * data rather than as open ground, which is the one thing a scene about a real district
 * cannot afford.
 *
 * The whole disc is grass. Not the dry alluvial soil it was — that is what this part of
 * Punjab actually is between its buildings, but bare earth and a bare *canvas* are the
 * same colour at a distance, so the honest surface was still reading as absence. Green
 * everywhere is the version that can never be mistaken for a hole in the render.
 *
 * Four scales of noise run over it — field-sized patches of dry and lush, plot-sized
 * clumping, a tuft scatter, and a fine grain — so the lawn is a lawn rather than a flat
 * green fill. The occupancy grid still shades it: parks are watered and darker, the
 * institutional compounds are mown, and the carriageway strip stays dull under the road
 * mesh so no bright edge shows at the kerb.
 */
const GROUND_FRAGMENT_BODY = /* glsl */ `
{
  vec2 world = vGroundWorld.xz;

  // Four scales, coarse to fine. Together they are what stops a single green from
  // reading as paper.
  float region = groundNoise(world * 0.0016);
  float plot = groundNoise(world * 0.011);
  float tuft = groundNoise(world * 0.055);
  float grain = groundNoise(world * 0.19);
  // Blade scale, ~1.5 m. This is the octave that reads as grass rather than as green
  // paint, and it is the only one that survives being looked at from close to.
  float blade = groundNoise(world * 0.67);

  // Dry summer grass against watered grass. Both are green; the difference is how much
  // yellow is in them, which is the whole range a lawn moves through in a Lahore June.
  vec3 dry  = vec3(0.086, 0.104, 0.040);
  vec3 lush = vec3(0.042, 0.098, 0.038);
  vec3 albedo = mix(dry, lush, region);

  // Plot-scale patchiness: one stretch waters better than the next.
  albedo = mix(albedo, albedo * 1.22, plot);
  // Tufts and clumping, which is the scale the eye actually reads as "grass".
  albedo = mix(albedo, albedo * 0.74, smoothstep(0.52, 0.92, tuft) * 0.7);

  // Inside the mapped extract the occupancy grid says what this cell actually is.
  if (abs(world.x) < uExtent && abs(world.y) < uExtent) {
    vec2 uv = (world + uExtent) / (2.0 * uExtent);
    vec4 cell = texture2D(uOccupancy, uv);

    // Park: watered, deeper, and a little bluer than the grass around it.
    vec3 park = mix(vec3(0.030, 0.080, 0.030), vec3(0.052, 0.116, 0.044), plot);
    albedo = mix(albedo, park, cell.g * 0.85);

    // Institutional grounds are mown, so they are more even than everything else — the
    // tuft noise is mixed back out rather than a different colour being mixed in.
    vec3 mown = mix(vec3(0.050, 0.100, 0.038), vec3(0.070, 0.124, 0.048), region);
    albedo = mix(albedo, mown, cell.b * 0.6);

    // Under the carriageway. The road mesh is drawn over this, so this only has to stop a
    // bright strip of grass showing at the kerb line.
    albedo = mix(albedo, albedo * 0.42, cell.r * 0.8);
  }

  // Fine grain and blade scatter last, over everything, so no surface anywhere is a
  // solid fill at any distance the camera can get to.
  albedo *= 0.88 + grain * 0.26;
  albedo *= 0.90 + blade * 0.22;

  diffuseColor.rgb = albedo;
}
`;

/**
 * Patch a Lambert material to draw the district's ground.
 *
 * Takes the light and the shadows exactly as before — this only decides the albedo.
 */
export function makeGroundMaterial(occupancy: Occupancy): THREE.MeshLambertMaterial {
  const material = new THREE.MeshLambertMaterial({ color: "#ffffff" });
  const uniforms = {
    uOccupancy: { value: occupancyTexture(occupancy) },
    uExtent: { value: EXTENT },
  };

  material.onBeforeCompile = (shader) => {
    shader.uniforms.uOccupancy = uniforms.uOccupancy;
    shader.uniforms.uExtent = uniforms.uExtent;

    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", `#include <common>\n${GROUND_VERTEX_HEAD}`)
      .replace("#include <begin_vertex>", `#include <begin_vertex>\n${GROUND_VERTEX_BODY}`);

    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>\n${GROUND_FRAGMENT_HEAD}`)
      .replace("#include <color_fragment>", `#include <color_fragment>\n${GROUND_FRAGMENT_BODY}`);
  };

  material.customProgramCacheKey = () => "terrarium-ground-v2";
  return material;
}
