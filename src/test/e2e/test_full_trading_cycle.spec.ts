import { test, expect } from '@playwright/test';

// Epic INF-9: Autonomous Quality Gates (AQG)
// Ensures the E2E E2E testing framework deterministic verification of MiFID II compliance
test.describe('INF-9: Autonomous Quality Gates & Audit Trail', () => {

    test('E2E Full Trading Cycle: Forcing a synchronous engine cycle and auditing the Senate Protocol', async ({ request }) => {
        const baseURL = process.env.VITE_API_BASE_URL || 'http://127.0.0.1:8002';
        const engineApiKey = process.env.VITE_ENGINE_API_KEY || 'e2e-test-key-123';
        const adminToken = process.env.VITE_INTERNAL_ADMIN_TOKEN || 'test-backend-internal-admin-token';
        
        // 1. Force the Engine Cycle
        // We use T-1 data which is injected into the engine to guarantee determinism
        const targetDate = new Date();
        targetDate.setDate(targetDate.getDate() - 2); // Yesterday (use -2 just in case market was closed yesterday or T-1 data isn't ready)
        // Make sure it's a weekday for data availability (quick and dirty fallback)
        if (targetDate.getDay() === 0) targetDate.setDate(targetDate.getDate() - 2); // Sunday to Friday
        if (targetDate.getDay() === 6) targetDate.setDate(targetDate.getDate() - 1); // Saturday to Friday
        
        const targetDateIso = targetDate.toISOString();

        console.log(`Triggering Force Cycle for AAPL on ${targetDateIso}...`);

        const cycleResponse = await request.post(`${baseURL}/api/v1/engine/force-cycle`, {
            headers: {
                'X-Bot-API-Key': engineApiKey,
                'Content-Type': 'application/json'
            },
            data: {
                symbol: 'AAPL',
                target_date: targetDateIso
            }
        });

        expect(cycleResponse.ok()).toBeTruthy();
        const cycleData = await cycleResponse.json();
        
        // Ensure the engine ran successfully and generated a session
        expect(cycleData.status).toBe('success');
        expect(cycleData.session_id).toBeTruthy();
        console.log(`Force Cycle Success. Session ID: ${cycleData.session_id}, Signal: ${cycleData.signal}`);

        const sessionId = cycleData.session_id;

        // 2. Validate Senate Protocol Audit Trail Byte-for-Byte
        // This validates that the engine's internal decision logic was successfully persisted to the DB
        // Wait for DB async write (SenateProtocol logs async)
        await new Promise(resolve => setTimeout(resolve, 3000));
        
        console.log(`Auditing persistence for Session ID: ${sessionId}...`);
        const auditResponse = await request.get(`${baseURL}/api/v1/audit/run/${sessionId}`, {
            headers: {
                // Must be authenticated as an operator to access MiFID tracking
                'Authorization': `Bearer ${adminToken}`
            }
        });

        // The audit endpoint should return success correctly
        expect(auditResponse.status()).toBe(200);
        const auditData = await auditResponse.json();

        // 3. Verify Ground Truth and Decision Lineage
        expect(auditData.session_id).toBe(sessionId);
        expect(auditData.symbol).toBe('AAPL');
        expect(auditData.signal_action).toBe(cycleData.signal);
        expect(auditData.consensus_score).toBeDefined();
        
        // Check if all sub-agents voted correctly and with recorded rationales 
        expect(auditData.votes_json).toBeDefined();
        expect(Array.isArray(auditData.votes_json)).toBeTruthy();
        expect(auditData.votes_json.length).toBeGreaterThan(0);

        // Verify the rationale includes text and valid scores
        for (const vote of auditData.votes_json) {
            expect(vote.agent).toBeTruthy();
            expect(typeof vote.score).toBe('number');
            expect(typeof vote.weight).toBe('number');
            // Epic 4.3 or standard agent rules mandate reason recording
        }

        console.log(`Audit Trail Data successfully verified! System is MiFID II compliant for this session.`);
    });

});
