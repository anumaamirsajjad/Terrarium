/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the Terrarium API. Defaults to http://127.0.0.1:8000. */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
