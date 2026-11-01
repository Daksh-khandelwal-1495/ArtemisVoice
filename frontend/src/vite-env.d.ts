/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ARTEMIS_API_URL?: string;
  readonly VITE_STUDIO_UI_URL?: string;
  readonly VITE_STUDIO_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
