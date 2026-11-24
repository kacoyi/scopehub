// future free
// Inspect public names only. Paint the frozen volume when each host API is absent.
const surfaces = [
  typeof window, typeof document, typeof fetch, typeof XMLHttpRequest,
  typeof WebSocket, typeof Worker, typeof importScripts, typeof postMessage,
  typeof process, typeof require, typeof Deno, typeof Bun, typeof localStorage,
  typeof indexedDB, typeof navigator, typeof showOpenFilePicker, typeof confirm,
  typeof __TAURI__, typeof __TAURI_INTERNALS__
];
if (!surfaces.every(kind => kind === "undefined")) throw Error("Host API visible");
rect(48, 64, rows[0].volume, 18, 0);
