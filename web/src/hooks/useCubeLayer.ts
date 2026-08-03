/**
 * Fetch and decode one cube layer, keyed by variable and window.
 *
 * Layers are ~163 kB and immutable for a given (variable, window), so they are cached
 * for the session. Re-fetching on every window toggle would make the compare slider feel
 * like a network operation when it is really a texture swap.
 */

import { useEffect, useState } from "react";

import { type LayerResponse, api } from "../api/client";
import { type Raster, decodeLayer } from "../raster/decode";

export interface LoadedLayer {
  layer: LayerResponse;
  raster: Raster;
}

const cache = new Map<string, LoadedLayer>();

export interface LayerState {
  data: LoadedLayer | null;
  loading: boolean;
  error: string | null;
}

export async function loadLayer(variable: string, window: string): Promise<LoadedLayer> {
  const key = `${variable}@${window}`;
  const cached = cache.get(key);
  if (cached) return cached;

  const layer = await api.layer(variable, window);
  const loaded: LoadedLayer = { layer, raster: decodeLayer(layer) };
  cache.set(key, loaded);
  return loaded;
}

export function useCubeLayer(variable: string | null, window: string | null): LayerState {
  const [state, setState] = useState<LayerState>({ data: null, loading: false, error: null });

  useEffect(() => {
    if (!variable || !window) {
      setState({ data: null, loading: false, error: null });
      return;
    }

    let cancelled = false;
    setState((current) => ({ ...current, loading: true, error: null }));

    loadLayer(variable, window)
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error: Error) => {
        if (!cancelled) setState({ data: null, loading: false, error: error.message });
      });

    return () => {
      cancelled = true;
    };
  }, [variable, window]);

  return state;
}
