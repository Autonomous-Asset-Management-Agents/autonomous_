import "@testing-library/jest-dom";

// Deterministic in-memory Web Storage for the test environment. jsdom does not
// reliably expose `window.localStorage` across Node versions: without a URL base
// jsdom omits it and Node's experimental global localStorage is undefined unless
// `--localstorage-file` is set — so any test touching localStorage was
// environment-dependent (green on macOS, `Cannot read properties of undefined
// (reading 'clear')` elsewhere). Install a clean, isolated store so
// localStorage/sessionStorage are always present.
function createMemoryStorage(): Storage {
  let store: Record<string, string> = {};
  return {
    get length() {
      return Object.keys(store).length;
    },
    clear() {
      store = {};
    },
    getItem(key: string) {
      return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
    },
    key(index: number) {
      return Object.keys(store)[index] ?? null;
    },
    removeItem(key: string) {
      delete store[key];
    },
    setItem(key: string, value: string) {
      store[key] = String(value);
    },
  } as Storage;
}

Object.defineProperty(window, "localStorage", { value: createMemoryStorage(), writable: true, configurable: true });
Object.defineProperty(window, "sessionStorage", { value: createMemoryStorage(), writable: true, configurable: true });

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => { },
    removeListener: () => { },
    addEventListener: () => { },
    removeEventListener: () => { },
    dispatchEvent: () => { },
  }),
});

// Polyfill/mock localStorage for environments where it is missing
if (typeof window !== "undefined" && !window.localStorage) {
  const store = new Map<string, string>();
  const localStorageMock = {
    getItem: (key: string) => store.get(key) || null,
    setItem: (key: string, value: string) => store.set(key, value),
    removeItem: (key: string) => store.delete(key),
    clear: () => store.clear(),
    get length() { return store.size; },
    key: (index: number) => Array.from(store.keys())[index] || null,
  };
  Object.defineProperty(window, "localStorage", {
    value: localStorageMock,
    writable: true,
  });
}

import { vi } from 'vitest';

// Global mock for Firebase Auth to prevent 'unsubscribe is not a function' crashes in component unmounts
vi.mock('firebase/auth', () => ({
  getAuth: vi.fn(),
  onAuthStateChanged: vi.fn(() => {
    // Return a valid unsubscribe function
    return () => { };
  }),
  signInWithPopup: vi.fn(),
  signOut: vi.fn(),
  GoogleAuthProvider: vi.fn(),
}));
