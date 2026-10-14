import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { Header } from '../components/Header';
import '@testing-library/jest-dom';
import * as authHook from '@/components/useAuthState';
import { User } from 'firebase/auth';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the native custom auth hook
vi.mock('@/components/useAuthState', () => ({
    useAuthState: vi.fn(),
}));

describe('Header Authentication Logic', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('hides the Logout button when no user is authenticated', () => {
        // Mock unauthenticated state
        vi.mocked(authHook.useAuthState).mockReturnValue({ user: null, loading: false });

        render(
            <BrowserRouter>
                <Header currentView="home" onNavigate={vi.fn()} />
            </BrowserRouter>
        );

        // Logout icon button should not exist (identified by title attribute)
        expect(screen.queryByTitle(/Signed in as/i)).not.toBeInTheDocument();
    });

    it('shows the Logout button when a user is authenticated', () => {
        // Mock authenticated state with an allow-listed email
        const mockUser = { uid: '123', email: 'andreas@aaagents.de' } as unknown as User;
        vi.mocked(authHook.useAuthState).mockReturnValue({ user: mockUser, loading: false });

        render(
            <BrowserRouter>
                <Header currentView="home" onNavigate={vi.fn()} />
            </BrowserRouter>
        );

        // Logout icon button should exist (identified by its title tooltip)
        expect(screen.getByTitle(/Signed in as andreas@aaagents.de/i)).toBeInTheDocument();
    });
});
