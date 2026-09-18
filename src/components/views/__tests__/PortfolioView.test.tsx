import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PortfolioView } from '../PortfolioView';
import type { Position } from '@/lib/api';

describe('PortfolioView', () => {
  const mockPositions: Position[] = [
    {
      symbol: 'AAPL',
      qty: 10,
      avg_entry_price: 150,
      market_value: 1600,
      unrealized_pnl: 100,
      unrealized_pnl_pct: 0.06,
      current_price: 160,
      change_today: 5,
    },
    {
      symbol: 'MSFT',
      qty: 5,
      avg_entry_price: 300,
      market_value: 1600,
      unrealized_pnl: 100,
      unrealized_pnl_pct: 0.06,
      current_price: 320,
      change_today: 10,
    }
  ];

  it('renders "Unrealized P&L" and calculates from positions when equity is undefined', () => {
    render(<PortfolioView positions={mockPositions} />);
    
    // Total value: 1600 + 1600 = $3,200.00
    expect(screen.getByText('$3,200.00')).toBeInTheDocument();
    
    // Unrealized P&L: 100 + 100 = $200.00
    expect(screen.getByText('Unrealized P&L')).toBeInTheDocument();
    expect(screen.getByText('+$200.00')).toBeInTheDocument();
  });

  it('renders "Total P&L" and calculates using startingCapital when equity is defined', () => {
    // We pass equity = 105000, and startingCapital = 100000
    render(<PortfolioView positions={mockPositions} equity={105000} startingCapital={100000} />);
    
    // Total value should be the passed equity: $105,000.00
    expect(screen.getByText('$105,000.00')).toBeInTheDocument();
    
    // Total P&L should be 105000 - 100000 = 5000
    expect(screen.getByText('Total P&L')).toBeInTheDocument();
    expect(screen.getByText('+$5,000.00')).toBeInTheDocument();
  });

  it('uses default startingCapital (100000) when equity is defined but startingCapital is omitted', () => {
    render(<PortfolioView positions={mockPositions} equity={95000} />);
    
    expect(screen.getByText('$95,000.00')).toBeInTheDocument();
    
    expect(screen.getByText('Total P&L')).toBeInTheDocument();
    expect(screen.getByText('-$5,000.00')).toBeInTheDocument(); // 95000 - 100000 = -5000
  });

  it('renders empty state when positions are empty', () => {
    render(<PortfolioView positions={[]} />);
    expect(screen.getByText(/No positions. Start the engine/i)).toBeInTheDocument();
  });
});
