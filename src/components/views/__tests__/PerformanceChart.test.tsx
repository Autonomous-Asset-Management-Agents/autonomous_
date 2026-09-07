import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PerformanceChart } from '../PerformanceChart';

vi.mock('recharts', async () => {
  const originalModule = await vi.importActual('recharts');
  return {
    ...originalModule as Record<string, unknown>,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div style={{ width: 800, height: 400 }}>{children}</div>
    ),
  };
});
import React from 'react';

// Mock ResizeObserver for recharts
class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver = ResizeObserver;

describe('PerformanceChart', () => {
  it('renders loading state when isLoading is true', () => {
    render(<PerformanceChart data={[]} isLoading={true} />);
    // In our implementation, we'll use a skeleton or text "Loading chart..."
    expect(screen.getByTestId('chart-loading')).toBeInTheDocument();
  });

  it('renders empty state when data is empty', () => {
    render(<PerformanceChart data={[]} isLoading={false} />);
    expect(screen.getByText(/No data available/i)).toBeInTheDocument();
  });

  it('filters data based on selected time range', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-05-15T12:00:00Z'));

    // Generate mock data for the last 60 days
    const mockData = [];
    const now = new Date();
    for (let i = 60; i >= 0; i--) {
      const d = new Date(now);
      d.setDate(d.getDate() - i);
      mockData.push({
        date: d.toISOString().split('T')[0],
        equity: 10000 + i * 10,
      });
    }

    render(<PerformanceChart data={mockData} isLoading={false} />);

    // Default might be '1M' or 'MAX'
    // Let's click '1D' and check if we show stats correctly.
    // We can't easily assert recharts internal SVG paths in JSDOM,
    // but we can assert that the high/low stats change.

    const maxBtn = screen.getByRole('button', { name: /Max/i });
    fireEvent.click(maxBtn);

    // For 60 days, High is 10600, Low is 10000
    expect(screen.getAllByText(/\$10,600\.00/i).length).toBeGreaterThan(0); // High/Open
    expect(screen.getAllByText(/\$10,000\.00/i).length).toBeGreaterThan(0); // Low

    const oneMonthBtn = screen.getByRole('button', { name: /1M/i });
    fireEvent.click(oneMonthBtn);

    // For 1M (30 days), High is 10300, Low is 10000
    expect(screen.getAllByText(/\$10,300\.00/i).length).toBeGreaterThan(0); // High/Open
    expect(screen.getAllByText(/\$10,000\.00/i).length).toBeGreaterThan(0); // Low

    vi.useRealTimers();
  });
});
