import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { PrivateRoute } from '../components/PrivateRoute';
import * as authHook from '@/components/useAuthState';
import * as desktopBridge from '@/lib/desktopBridge';
import { User } from 'firebase/auth';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import '@testing-library/jest-dom';

vi.mock('@/components/useAuthState', () => ({
    useAuthState: vi.fn(),
}));

vi.mock('@/lib/desktopBridge', () => ({
    isDesktop: vi.fn(() => false),
}));

// Mock firebase signOut
vi.mock('firebase/auth', () => ({
    signOut: vi.fn(),
    getAuth: vi.fn(),
    onAuthStateChanged: vi.fn(() => vi.fn()),
    GoogleAuthProvider: vi.fn(),
}));

describe('PrivateRoute', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        // Default to the cloud build (browser). Desktop tests opt in explicitly.
        vi.mocked(desktopBridge.isDesktop).mockReturnValue(false);
    });

    const renderRoute = () => {
        return render(
            <MemoryRouter initialEntries={['/protected']}>
                <Routes>
                    <Route path="/login" element={<div>Login Page</div>} />
                    <Route
                        path="/protected"
                        element={
                            <PrivateRoute>
                                <div>Protected Content</div>
                            </PrivateRoute>
                        }
                    />
                </Routes>
            </MemoryRouter>
        );
    };

    it('redirects to /login when unauthenticated', async () => {
        // user is null, loading is false
        vi.mocked(authHook.useAuthState).mockReturnValue({ user: null, loading: false });

        renderRoute();

        await waitFor(() => {
            expect(screen.getByText('Login Page')).toBeInTheDocument();
        });
        expect(screen.queryByText('Protected Content')).not.toBeInTheDocument();
    });

    it('renders children when authenticated with allowed email', async () => {
        // Ensuring the email fulfills the allow-list logic inside PrivateRoute
        const mockUser = { uid: '123', email: 'andreas@aaagents.de' } as unknown as User;
        // user is mockUser, loading is false
        vi.mocked(authHook.useAuthState).mockReturnValue({ user: mockUser, loading: false });
        renderRoute();

        await waitFor(() => {
            expect(screen.getByText('Protected Content')).toBeInTheDocument();
        });
    });

    // ── Desktop bypass (#1050) ──────────────────────────────────────────────
    // The Electron desktop edition has no Firebase auth — secrets are held in
    // the OS keychain and the engine runs locally. PrivateRoute must therefore
    // render its children unconditionally on desktop, never the Firebase login
    // wall. (The cloud build keeps the full auth + allowlist gate above.)
    it('renders children on desktop even when unauthenticated', async () => {
        vi.mocked(desktopBridge.isDesktop).mockReturnValue(true);
        vi.mocked(authHook.useAuthState).mockReturnValue({ user: null, loading: false });

        renderRoute();

        await waitFor(() => {
            expect(screen.getByText('Protected Content')).toBeInTheDocument();
        });
        expect(screen.queryByText('Login Page')).not.toBeInTheDocument();
    });

    it('does not redirect to /login on desktop while auth is still loading', async () => {
        vi.mocked(desktopBridge.isDesktop).mockReturnValue(true);
        vi.mocked(authHook.useAuthState).mockReturnValue({ user: null, loading: true });

        renderRoute();

        await waitFor(() => {
            expect(screen.getByText('Protected Content')).toBeInTheDocument();
        });
        expect(screen.queryByText('Login Page')).not.toBeInTheDocument();
    });
});
