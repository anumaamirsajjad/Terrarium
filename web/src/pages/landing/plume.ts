/**
 * The plume, as arithmetic rather than as a mood.
 *
 * A Gaussian plume from a ground-level line source, with the ground and the mixing lid as
 * reflecting boundaries — which is the model `cores/air.py` runs, minus the FFT that lets
 * it do the whole tile at once. Keeping it analytic buys the one thing the previous
 * particle sketch could not: the seasonal factor on screen is *computed from the two
 * seasons' physical constants*, not typed into a caption above an animation that happened
 * to look denser.
 *
 * The reflections are an image series. Summing enough images makes the near-source Gaussian
 * and the far-field well-mixed slab one expression instead of two cases with a seam:
 * once sigma exceeds the lid, the images overlap into a uniform column and the formula
 * collapses to Q / (u * lid) on its own.
 *
 * Uncalibrated, and it stays that way. The constants below are literature figures for the
 * boundary layer over a South Asian city at the ~10:30 overpass hour, in the same category
 * as the core's emission factors. What this reproduces is the *ratio*, and that ratio is
 * the measured one.
 */

export interface Season {
  key: "summer" | "winter";
  name: string;
  /** Mixing height, m — the lid. Well mixed by mid-morning in summer; an inversion in winter. */
  lidM: number;
  /** Wind at the overpass hour, m/s. The winter season's air is slack as well as capped. */
  windMs: number;
  /** Coefficient in sigma_z = spread * x^0.78. Unstable air spreads faster. */
  spread: number;
  note: string;
}

/** The tile's own width. Concentrations are read at the downwind edge of it. */
export const DOMAIN_X_M = 20_000;
/** How much sky the slice shows. Both lids have to fit, with headroom above the taller. */
export const DOMAIN_Z_M = 1_000;

export const SUMMER: Season = {
  key: "summer",
  name: "summer",
  lidM: 800,
  windMs: 2.6,
  spread: 0.28,
  note: "deep, well mixed by mid-morning",
};

export const WINTER: Season = {
  key: "winter",
  name: "winter",
  lidM: 250,
  windMs: 1.1,
  spread: 0.11,
  note: "inversion, and a slacker wind",
};

export const SEASONS: readonly Season[] = [SUMMER, WINTER];

/** Vertical spread at distance `xM` downwind. Pasquill's power law. */
export function sigmaZ(xM: number, season: Season): number {
  return season.spread * Math.pow(Math.max(xM, 1), 0.78);
}

/**
 * Concentration at `xM` downwind, `zM` above ground, for a unit source (1 g/s per metre of
 * road). Arbitrary units — only ratios between the two seasons mean anything.
 *
 * Seven images: three either side of the layer. That is enough for the series to converge
 * to the well-mixed slab by the time sigma reaches the lid, which is where it matters.
 */
export function concentration(xM: number, zM: number, season: Season): number {
  if (zM > season.lidM) return 0;

  const sigma = sigmaZ(xM, season);
  const gauss = (dz: number) => Math.exp(-(dz * dz) / (2 * sigma * sigma));

  let images = 0;
  for (let k = -3; k <= 3; k++) {
    const centre = 2 * k * season.lidM;
    // The source sits on the ground, so its ground image lands on top of it: both terms
    // of each pair are the same distance from a mirrored plume centre.
    images += gauss(zM - centre) + gauss(zM + centre);
  }

  return images / (Math.sqrt(2 * Math.PI) * sigma * season.windMs);
}

/** What a kerbside monitor at the downwind edge of the tile would breathe. */
export function groundLevel(season: Season): number {
  return concentration(DOMAIN_X_M, 0, season);
}

/**
 * The slice as a `width * height` field, row 0 at the top of the sky.
 *
 * Static per season, so it is built once and blitted every frame. Normalised against the
 * strongest season so the two panels share one exposure — a per-panel normalisation would
 * make both look equally loaded, which is the entire thing this visualisation exists to
 * disprove.
 */
export function slice(season: Season, width: number, height: number): Float32Array {
  const out = new Float32Array(width * height);
  const norm = concentration(1_500, 0, WINTER);

  for (let row = 0; row < height; row++) {
    const zM = DOMAIN_Z_M * (1 - (row + 0.5) / height);
    for (let col = 0; col < width; col++) {
      const xM = DOMAIN_X_M * ((col + 0.5) / width);
      out[row * width + col] = Math.min(1, concentration(xM, zM, season) / norm);
    }
  }
  return out;
}

/**
 * Fold a vertical offset into the layer by mirroring off the ground and the lid.
 *
 * The particles are decoration, but they are decoration that has to agree with the field
 * underneath them: reflecting them is the same boundary condition the image series encodes,
 * so a mote never sits where the maths says there is nothing.
 */
export function reflect(zM: number, lidM: number): number {
  const period = 2 * lidM;
  const folded = Math.abs(zM) % period;
  return folded <= lidM ? folded : period - folded;
}
