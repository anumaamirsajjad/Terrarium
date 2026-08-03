/**
 * Display helpers for cube units.
 *
 * The cube follows the CF convention where a dimensionless quantity has units `"1"`.
 * That is correct in the metadata and nonsense on screen: NDVI's legend read
 * "-0.45 1 … 0.78 1", which looks like a rendering bug rather than a unit. Suppress it
 * at the display boundary rather than lying about it in `state/cube.py`.
 */

/** CF-convention marker for a dimensionless quantity. */
const DIMENSIONLESS = "1";

/** The unit as it should appear beside a number — empty when there is nothing to say. */
export function formatUnits(units: string): string {
  return units === DIMENSIONLESS ? "" : units;
}

/** "lst_c (degC)", but "ndvi" rather than "ndvi (1)". */
export function labelWithUnits(name: string, units: string): string {
  const suffix = formatUnits(units);
  return suffix ? `${name} (${suffix})` : name;
}

/** Join a value and its unit, without a trailing space when dimensionless. */
export function withUnits(value: string, units: string): string {
  const suffix = formatUnits(units);
  return suffix ? `${value} ${suffix}` : value;
}
