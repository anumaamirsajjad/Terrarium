/**
 * The citizen-report raster, fetched from `/observations/layer`.
 *
 * Deliberately *not* part of `useCubeLayer`, for the same reason the endpoint is not part
 * of `/cube` (D19): every cube variable is an instrument reading with a known error, and a
 * language model's reading of a phone photo is not. Sharing the loader would be the first
 * step towards sharing the cache, the legend and eventually the question "what does the
 * cube say".
 *
 * Two concrete differences from a cube layer, both of which fall out of that:
 *
 * - **Never cached.** A cube layer is immutable for a given (variable, window); this one
 *   changes the moment somebody submits a photo, so it refetches whenever the report count
 *   moves.
 * - **No window.** Reports are not composited into a seasonal window; they are whatever
 *   has been submitted since the process started, and they vanish when it restarts.
 */

import { useEffect, useState } from "react";

import { api } from "../api/client";
import { decodeLayer } from "../raster/decode";
import type { LoadedLayer } from "./useCubeLayer";

export interface ObservationLayerState {
  data: LoadedLayer | null;
  loading: boolean;
  error: string | null;
  /** How many reports the raster was built from. Zero means an empty, all-NaN grid. */
  count: number;
}

const IDLE: ObservationLayerState = { data: null, loading: false, error: null, count: 0 };

/**
 * @param active   Whether the layer is actually being shown. Nothing is fetched otherwise.
 * @param revision Bump to refetch — pass the report count, so a new photo redraws the map.
 */
export function useObservationLayer(active: boolean, revision: number): ObservationLayerState {
  const [state, setState] = useState<ObservationLayerState>(IDLE);

  useEffect(() => {
    if (!active) {
      setState(IDLE);
      return;
    }

    let cancelled = false;
    setState((current) => ({ ...current, loading: true, error: null }));

    api
      .observationLayer()
      .then((response) => {
        if (cancelled) return;
        setState({
          data: { layer: response.layer, raster: decodeLayer(response.layer) },
          loading: false,
          error: null,
          count: response.count,
        });
      })
      .catch((error: Error) => {
        if (!cancelled) setState({ ...IDLE, error: error.message });
      });

    return () => {
      cancelled = true;
    };
  }, [active, revision]);

  return state;
}
