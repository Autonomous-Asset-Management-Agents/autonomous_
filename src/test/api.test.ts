import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as api from '../lib/api';

// Mock the firebase module so auth is defined
vi.mock('../lib/firebase', () => ({
  auth: {
    currentUser: null,
  },
}));

describe('api.ts fetchJson', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn().mockResolvedValue({
      json: vi.fn().mockResolvedValue({ status: 'success' }),
      status: 200,
    });
  });

  it('adds OSS dummy token when auth.currentUser is null', async () => {
    await api.fetchStrategy(); // This calls fetchJson internally

    // Verify fetch was called with the dummy token
    expect(global.fetch).toHaveBeenCalled();
    const fetchCall = vi.mocked(global.fetch).mock.calls[0];
    const fetchOptions = fetchCall[1] as RequestInit;
    const headers = fetchOptions.headers as Record<string, string>;

    expect(headers).toBeDefined();
    expect(headers['Authorization']).toBe('Bearer oss-mode-bypass');
  });

  describe('getApiBase', () => {
    const originalEnv = import.meta.env;
    const originalWindow = global.window;

    beforeEach(() => {
      vi.resetModules();
      // Setup minimal mock window
      global.window = Object.create(window);
      Object.defineProperty(window, 'location', {
        value: {
          hostname: 'localhost',
          host: 'localhost:3000',
          protocol: 'http:',
          search: '',
        },
        writable: true,
      });
    });

    afterEach(() => {
      global.window = originalWindow;
      // Revert env variables
      import.meta.env.DEV = originalEnv.DEV;
      delete import.meta.env.VITE_PUBLIC_API_URL;
    });

    it('returns /api in DEV mode for Vite Proxy (OSS local)', () => {
      import.meta.env.DEV = true;
      const apiBase = api.getApiBase();
      expect(apiBase).toBe('/api');
    });

    it('falls back to default public API URL when VITE_PUBLIC_API_URL is undefined on aaagents.de', () => {
      import.meta.env.DEV = false;
      window.location.hostname = 'aaagents.de'; // triggers isPublicViewOnly()
      const apiBase = api.getApiBase();
      expect(apiBase).toBe('https://api.aaagents.de');
    });

    it('uses VITE_PUBLIC_API_URL when set and on aaagents.de', () => {
      import.meta.env.DEV = false;
      import.meta.env.VITE_PUBLIC_API_URL = 'https://aaa-api-public-test.a.run.app';
      
      window.location.hostname = 'aaagents.de'; // triggers isPublicViewOnly()
      const apiBase = api.getApiBase();
      expect(apiBase).toBe('https://aaa-api-public-test.a.run.app');
    });
  });
});
