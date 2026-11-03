-- Migration: MiFID II Reasoning Trace Logging (Epic 2.2)
-- Adds the reasoning_trace column to the decisions table to preserve the ML explainability string
-- for every trading decision (used for MiFID II audits).

ALTER TABLE decisions ADD COLUMN IF NOT EXISTS reasoning_trace TEXT;
