import React from "react";
import { Check, Shield, Server, Zap, Building } from "lucide-react";
import { Button } from "@/components/ui/button";

const PricingPreview = () => {
  return (
    <div className="min-h-screen pt-24 pb-24 px-4 sm:px-6 lg:px-8" style={{ background: "#000", color: "#fff" }}>
      <div className="max-w-7xl mx-auto text-center">
        <div className="mb-4 text-[#00c27a] font-mono text-sm uppercase tracking-widest">
          aaagents.de · Editions
        </div>
        <h2 className="text-3xl font-extrabold tracking-tight sm:text-4xl lg:text-5xl mb-4" style={{ fontFamily: "var(--lb-sans, Inter)" }}>
          Choose your Edition
        </h2>
        <p className="mt-4 text-xl max-w-3xl mx-auto mb-16" style={{ color: "#9a9a9a" }}>
          AAAgents provides the most powerful autonomous trading AI – tailored to your requirements. 
          All editions share one ironclad principle: <span className="text-white font-semibold">100% Execution-Only.</span>
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          
          {/* Private Basic */}
          <div className="rounded-xl flex flex-col text-left transition-all" style={{ background: "#0b0b0b", border: "1px solid #1a1a1a", padding: "32px 24px" }}>
            <div className="flex items-center gap-3 mb-4">
              <Shield className="w-5 h-5 text-[#9a9a9a]" />
              <h3 className="text-lg font-bold">Private Basic</h3>
            </div>
            <p className="text-sm mb-6 h-10" style={{ color: "#9a9a9a" }}>For beginners to explore and test risk-free.</p>
            <div className="mb-6">
              <span className="text-4xl font-extrabold">0 €</span>
              <span style={{ color: "#9a9a9a" }}> / forever</span>
            </div>
            <ul className="space-y-3 mb-8 flex-1">
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> Desktop App (Win/macOS)</li>
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> Basic AI decisions (3 Agents)</li>
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> Risk-free Trading Simulation</li>
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> Test 12 months of past data</li>
            </ul>
            <Button variant="outline" className="w-full text-white" style={{ background: "transparent", borderColor: "#1a1a1a" }}>
              Download for Free
            </Button>
          </div>

          {/* Private Pro */}
          <div className="rounded-xl flex flex-col text-left relative overflow-hidden transition-all" style={{ background: "#0b0b0b", border: "1px solid rgba(0,194,122,0.3)", padding: "32px 24px", boxShadow: "0 0 40px -10px rgba(0,194,122,0.15)" }}>
            <div className="absolute top-0 right-0 text-xs font-bold px-3 py-1 rounded-bl-lg" style={{ background: "#00c27a", color: "#000" }}>
              POPULAR
            </div>
            <div className="flex items-center gap-3 mb-4">
              <Zap className="w-5 h-5" style={{ color: "#00c27a" }} />
              <h3 className="text-lg font-bold">Private Pro</h3>
            </div>
            <p className="text-sm mb-6 h-10" style={{ color: "#9a9a9a" }}>For active traders who want to trade with real money.</p>
            <div className="mb-6">
              <span className="text-4xl font-extrabold">14.99 €</span>
              <span style={{ color: "#9a9a9a" }}> / month</span>
            </div>
            <ul className="space-y-3 mb-8 flex-1">
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> Full AI Power (All 9 Agents)</li>
              <li className="flex gap-3 text-sm font-medium text-white"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> Live Trading (Real Money)</li>
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> Test unlimited past data</li>
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> Priority Support</li>
            </ul>
            <Button className="w-full" style={{ background: "#00c27a", color: "#000", fontWeight: "bold" }}>
              Upgrade to Pro
            </Button>
          </div>

          {/* Professional */}
          <div className="rounded-xl flex flex-col text-left transition-all" style={{ background: "#0b0b0b", border: "1px solid #1a1a1a", padding: "32px 24px" }}>
            <div className="flex items-center gap-3 mb-4">
              <Server className="w-5 h-5 text-[#9a9a9a]" />
              <h3 className="text-lg font-bold">Professional</h3>
            </div>
            <p className="text-sm mb-6 h-10" style={{ color: "#9a9a9a" }}>24/7 automated trading running privately in your own cloud.</p>
            <div className="mb-6 flex flex-col">
              <div>
                <span className="text-4xl font-extrabold">29 €</span>
                <span style={{ color: "#9a9a9a" }}> / month</span>
              </div>
              <span className="text-xs mt-1" style={{ color: "#737373" }}>+ Cloud hosting costs (e.g., AWS, Hetzner)</span>
            </div>
            <ul className="space-y-3 mb-8 flex-1">
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> Private Cloud Setup</li>
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> Professional Market Data</li>
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> AI Chat (Ask why it traded)</li>
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> 24/7 Fully Automated Trading</li>
            </ul>
            <Button variant="outline" className="w-full text-white" style={{ background: "transparent", borderColor: "#1a1a1a" }}>
              Purchase License
            </Button>
          </div>

          {/* Institutional */}
          <div className="rounded-xl flex flex-col text-left transition-all" style={{ background: "#0b0b0b", border: "1px solid #1a1a1a", padding: "32px 24px" }}>
            <div className="flex items-center gap-3 mb-4">
              <Building className="w-5 h-5 text-[#9a9a9a]" />
              <h3 className="text-lg font-bold">Institutional</h3>
            </div>
            <p className="text-sm mb-6 h-10" style={{ color: "#9a9a9a" }}>Secure and compliant setup for asset managers.</p>
            <div className="mb-6">
              <span className="text-4xl font-extrabold">Custom</span>
              <span style={{ color: "#9a9a9a" }}> Setup + SLA</span>
            </div>
            <ul className="space-y-3 mb-8 flex-1">
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> Dedicated Cloud Servers</li>
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> Tamper-proof Audit Logs (8 Yrs)</li>
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> MiFID II Ready & Multi-Account</li>
              <li className="flex gap-3 text-sm text-gray-300"><Check className="w-5 h-5 shrink-0" style={{ color: "#00c27a" }} /> Emergency Stop Button</li>
            </ul>
            <Button variant="outline" className="w-full text-white" style={{ background: "transparent", borderColor: "#1a1a1a" }}>
              Contact Sales
            </Button>
          </div>

        </div>

        <div className="mt-16 text-sm max-w-4xl mx-auto pt-8" style={{ borderTop: "1px solid #1a1a1a", color: "#9a9a9a" }}>
          <p>
            <strong className="text-white">Important regulatory notice (BaFin / KWG):</strong> AAAgents is strictly a technology provider. 
            We expressly do not provide investment advice, investment brokerage, or financial portfolio management. 
            Our software acts purely as a tool (Execution-Only) and is operated decentrally within the user's sphere of control (BYOC / local PC).
          </p>
        </div>
      </div>
    </div>
  );
};

export default PricingPreview;
