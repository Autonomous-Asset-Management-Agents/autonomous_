import React, { useState } from "react";
import { Check, Shield, Server, Building, TrendingUp, Search, Zap, Globe, Cpu } from "lucide-react";
import { Button } from "@/components/ui/button";

const data = [
  {
    tier: "DESKTOP VERSION",
    name: "Private Basic",
    price: "0.00 €",
    period: "/ forever",
    profitable: "3-agent consensus for safe, zero-risk strategy testing.",
    auditable: "Recorded in a manipulation-proof SHA-256 JSON log.",
    safe: "Your keys and strategies never leave your machine.",
    cta: "Download Basic"
  },
  {
    tier: "DESKTOP VERSION",
    name: "Private Pro",
    price: "14.99 €",
    period: "/ month",
    profitable: "Full 9-agent consensus for live signal generation.",
    auditable: "Follow trade reasoning strings via the local console.",
    safe: "Credentials encrypted via OS native secure storage.",
    cta: "Go Pro"
  },
  {
    tier: "DOCKER FIRST ARCHITECTURE",
    name: "Professional",
    price: "29.00 €",
    period: "/ month",
    extra: "+ Cloud hosting costs",
    profitable: "24/7 Headless Autopilot in any cloud environment.",
    auditable: "XAI Chat Interface to query your trading reasoning.",
    safe: "BYOC. Total infrastructure control on your servers.",
    cta: "Purchase Professional"
  },
  {
    tier: "CLOUD NATIVE ARCHITECTURE",
    name: "Institutional",
    price: "Custom",
    period: "Pricing",
    profitable: "Multi-Tenant Scaling for high-performance instances.",
    auditable: "Tamper-Proof WORM Audit with RTS 22 export APIs.",
    safe: "Isolated Corporate VPC with system-kill-switch.",
    cta: "Contact Us"
  }
];

export default function PricingCombinedPrivate() {
  const [privateMode, setPrivateMode] = useState("Basic");

  const currentPrivate = privateMode === "Basic" ? data[0] : data[1];

  return (
    <div className="min-h-screen bg-black text-white p-8 space-y-40 font-sans pb-40" style={{ backgroundColor: '#000' }}>
      
      {/* =========================================
          VARIATION A: THE DYNAMIC SWITCHER
          A single card for Private with a toggle.
          ========================================= */}
      <section className="max-w-7xl mx-auto px-4">
        <div className="text-center mb-16">
          <h2 className="text-4xl font-black mb-4">Variation A: The Dynamic Switcher</h2>
          <p className="text-[#9a9a9a] uppercase tracking-widest text-xs">Private as one card with internal toggle</p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* COMBINED PRIVATE CARD */}
          <div className="relative flex flex-col p-8 rounded-[20px] border border-white/10 bg-white/[0.015]">
            <div className="mb-8">
              <div className="text-[11px] font-mono tracking-[0.2em] mb-3 text-[#9a9a9a]">DESKTOP VERSION</div>
              <h3 className="text-3xl font-bold mb-6">Private Edition</h3>
              
              {/* Toggle */}
              <div className="flex p-1 bg-white/5 rounded-full w-fit mb-8 border border-white/5">
                <button 
                  onClick={() => setPrivateMode("Basic")}
                  className={`px-6 py-2 rounded-full text-xs font-bold transition-all ${privateMode === "Basic" ? "bg-white text-black" : "text-[#9a9a9a] hover:text-white"}`}
                >
                  Basic
                </button>
                <button 
                  onClick={() => setPrivateMode("Pro")}
                  className={`px-6 py-2 rounded-full text-xs font-bold transition-all ${privateMode === "Pro" ? "bg-white text-black" : "text-[#9a9a9a] hover:text-white"}`}
                >
                  Pro
                </button>
              </div>

              <div className="flex items-baseline gap-2">
                <span className="text-4xl font-black text-white">{currentPrivate.price}</span>
                <span className="text-xs text-[#737373]">{currentPrivate.period}</span>
              </div>
            </div>

            <div className="space-y-6 flex-1 mb-10">
              <div className="flex gap-4">
                <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5 shrink-0" />
                <p className="text-[13px] text-[#9a9a9a] leading-relaxed"><b>Profitable:</b> {currentPrivate.profitable}</p>
              </div>
              <div className="flex gap-4">
                <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5 shrink-0" />
                <p className="text-[13px] text-[#9a9a9a] leading-relaxed"><b>Auditable:</b> {currentPrivate.auditable}</p>
              </div>
              <div className="flex gap-4">
                <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5 shrink-0" />
                <p className="text-[13px] text-[#9a9a9a] leading-relaxed"><b>Safe:</b> {currentPrivate.safe}</p>
              </div>
            </div>

            <Button className="w-full py-7 rounded-full bg-white text-black font-bold hover:bg-gray-200">
              {currentPrivate.cta}
            </Button>
          </div>

          {/* PROFESSIONAL */}
          <div className="relative flex flex-col p-8 rounded-[20px] border border-white/30 overflow-hidden" style={{ background: 'linear-gradient(180deg, rgba(255, 255, 255, 0.05) 0%, rgba(255, 255, 255, 0) 50%), rgba(255, 255, 255, 0.015)' }}>
            <div className="absolute inset-0 h-[2px] opacity-70" style={{ background: 'linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.8) 50%, transparent)' }} />
            <div className="mb-10">
              <div className="text-[11px] font-mono tracking-[0.2em] mb-3 text-[#9a9a9a]">DOCKER FIRST ARCHITECTURE</div>
              <h3 className="text-3xl font-bold mb-2">Professional</h3>
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-black text-white">29.00 €</span>
                <span className="text-xs text-[#737373]">/ month</span>
              </div>
            </div>
            <div className="space-y-6 flex-1 mb-10">
                <p className="text-[13px] text-[#9a9a9a]"><span className="text-[#00c27a] font-bold">Profitable:</span> 24/7 Headless Autopilot.</p>
                <p className="text-[13px] text-[#9a9a9a]"><span className="text-[#00c27a] font-bold">Auditable:</span> XAI Chat Interface.</p>
                <p className="text-[13px] text-[#9a9a9a]"><span className="text-[#00c27a] font-bold">Safe:</span> Bring Your Own Cloud.</p>
            </div>
            <Button className="w-full py-7 rounded-full bg-white text-black font-bold hover:bg-gray-200">Purchase Professional</Button>
          </div>

          {/* INSTITUTIONAL */}
          <div className="relative flex flex-col p-8 rounded-[20px] border border-[#00c27a]/30 overflow-hidden" style={{ background: 'linear-gradient(180deg, rgba(0, 194, 122, 0.05) 0%, rgba(0, 194, 122, 0) 50%), rgba(255, 255, 255, 0.015)' }}>
            <div className="absolute inset-0 h-[2px] opacity-70" style={{ background: 'linear-gradient(90deg, transparent, #00c27a 50%, transparent)' }} />
            <div className="mb-10">
              <div className="text-[11px] font-mono tracking-[0.2em] mb-3 text-[#00c27a]">CLOUD NATIVE ARCHITECTURE</div>
              <h3 className="text-3xl font-bold mb-2">Institutional</h3>
              <div className="text-2xl font-black text-white">Custom Pricing</div>
            </div>
            <div className="space-y-6 flex-1 mb-10">
                <p className="text-[13px] text-[#9a9a9a]"><span className="text-[#00c27a] font-bold">Profitable:</span> Multi-Tenant Scaling.</p>
                <p className="text-[13px] text-[#9a9a9a]"><span className="text-[#00c27a] font-bold">Auditable:</span> WORM Audit Logs.</p>
                <p className="text-[13px] text-[#9a9a9a]"><span className="text-[#00c27a] font-bold">Safe:</span> Isolated Corporate VPC.</p>
            </div>
            <Button className="w-full py-7 rounded-full bg-[#00c27a] text-black font-bold hover:bg-[#00e08d] shadow-[0_0_24px_rgba(0,194,122,0.3)]">Contact Us</Button>
          </div>
        </div>
      </section>

      {/* =========================================
          VARIATION B: THE WIDE PRIVATE (1+2)
          A wide card at the top, others below.
          ========================================= */}
      <section className="max-w-7xl mx-auto px-4">
        <div className="text-center mb-16">
          <h2 className="text-4xl font-black mb-4">Variation B: The Wide Private Hub</h2>
          <p className="text-[#9a9a9a] uppercase tracking-widest text-xs">Private spans full width, others in grid</p>
        </div>

        <div className="space-y-6">
          {/* WIDE PRIVATE BOX */}
          <div className="p-12 rounded-[32px] border border-white/10 bg-white/[0.015] grid grid-cols-1 md:grid-cols-2 gap-12 items-center">
             <div>
                <div className="text-[11px] font-mono tracking-[0.2em] mb-4 text-[#9a9a9a]">DESKTOP VERSION</div>
                <h3 className="text-5xl font-bold mb-6">Private Edition</h3>
                <p className="text-[#9a9a9a] text-lg leading-relaxed mb-10">
                   The simplest entry to <span className="text-white font-bold">autonomous_</span> trading. Run it locally on your hardware, keep all keys in your hands.
                </p>
                <div className="flex flex-wrap gap-4">
                   <div className="flex-1 min-w-[200px] p-6 rounded-2xl bg-white/5 border border-white/5">
                      <div className="text-sm font-bold mb-1">Basic</div>
                      <div className="text-2xl font-black mb-4">0.00 €</div>
                      <Button className="w-full bg-white/10 text-white rounded-full text-xs">Get Basic</Button>
                   </div>
                   <div className="flex-1 min-w-[200px] p-6 rounded-2xl bg-white/10 border border-[#00c27a]/20 shadow-[0_0_20px_rgba(0,194,122,0.05)]">
                      <div className="text-sm font-bold text-[#00c27a] mb-1">Pro</div>
                      <div className="text-2xl font-black mb-4">14.99 €</div>
                      <Button className="w-full bg-[#00c27a] text-black rounded-full text-xs font-bold">Upgrade Pro</Button>
                   </div>
                </div>
             </div>
             <div className="grid grid-cols-1 gap-6">
                <div className="p-6 rounded-2xl bg-black border border-white/5">
                   <div className="text-[#00c27a] font-black text-[10px] mb-2 tracking-widest uppercase">Privacy First</div>
                   <p className="text-sm text-[#9a9a9a]">Everything stays local. No cloud storage, no data sharing. You own your alpha.</p>
                </div>
                <div className="p-6 rounded-2xl bg-black border border-white/5">
                   <div className="text-[#00c27a] font-black text-[10px] mb-2 tracking-widest uppercase">Seamless Setup</div>
                   <p className="text-sm text-[#9a9a9a]">Electron-based desktop app. Auto-installs embedded SQLite and Python runtime.</p>
                </div>
             </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
             {/* PROF + INST repeat style from above but half width */}
             <div className="relative flex flex-col p-10 rounded-[32px] border border-white/30 bg-[#0b0b0b] overflow-hidden">
                <div className="absolute inset-0 h-[2px] opacity-70" style={{ background: 'linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.8) 50%, transparent)' }} />
                <div className="text-[11px] font-mono tracking-[0.2em] mb-4 text-[#9a9a9a]">DOCKER FIRST</div>
                <h3 className="text-3xl font-bold mb-6">Professional</h3>
                <div className="flex-1 mb-8">
                   <div className="text-3xl font-black mb-1">29.00 €</div>
                   <div className="text-sm text-[#9a9a9a]">/ month</div>
                </div>
                <Button className="w-full py-7 rounded-full bg-white text-black font-bold">Get License</Button>
             </div>
             <div className="relative flex flex-col p-10 rounded-[32px] border border-[#00c27a]/30 bg-[#0b0b0b] overflow-hidden">
                <div className="absolute inset-0 h-[2px] opacity-70" style={{ background: 'linear-gradient(90deg, transparent, #00c27a 50%, transparent)' }} />
                <div className="text-[11px] font-mono tracking-[0.2em] mb-4 text-[#00c27a]">CLOUD NATIVE</div>
                <h3 className="text-3xl font-bold mb-6">Institutional</h3>
                <div className="flex-1 mb-8">
                   <div className="text-3xl font-black mb-1">Custom</div>
                   <div className="text-sm text-[#9a9a9a]">Enterprise Pricing</div>
                </div>
                <Button className="w-full py-7 rounded-full bg-[#00c27a] text-black font-bold">Talk to us</Button>
             </div>
          </div>
        </div>
      </section>

      {/* =========================================
          VARIATION C: THE FEATURE STACK (3-TIER GRID)
          Standard grid but Private has nested selection.
          ========================================= */}
      <section className="max-w-7xl mx-auto px-4">
        <div className="text-center mb-16">
          <h2 className="text-4xl font-black mb-4">Variation C: The Feature Stack</h2>
          <p className="text-[#9a9a9a] uppercase tracking-widest text-xs">Standard 3-column grid with tiered private options</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 items-stretch">
          {/* PRIVATE TIERED CARD */}
          <div className="flex flex-col p-8 rounded-[20px] border border-white/10 bg-white/[0.015]">
            <div className="text-[11px] font-mono tracking-[0.2em] mb-3 text-[#9a9a9a]">DESKTOP VERSION</div>
            <h3 className="text-3xl font-bold mb-10">Private Edition</h3>
            
            <div className="space-y-4 flex-1">
               {/* BASIC SUB-BOX */}
               <div className="p-5 rounded-2xl bg-black/40 border border-white/5 hover:border-white/20 transition-all cursor-pointer group">
                  <div className="flex justify-between items-center mb-1">
                     <span className="font-bold text-sm">Basic</span>
                     <span className="text-lg font-black tracking-tight">0.00 €</span>
                  </div>
                  <p className="text-[11px] text-[#737373] group-hover:text-[#9a9a9a] mb-4">3-Agent Paper Trading.</p>
                  <Button className="w-full h-10 bg-white/5 text-white hover:bg-white hover:text-black transition-all rounded-lg text-xs">Download</Button>
               </div>
               
               {/* PRO SUB-BOX */}
               <div className="p-5 rounded-2xl bg-[#00c27a]/5 border border-[#00c27a]/20 hover:bg-[#00c27a]/10 transition-all cursor-pointer group">
                  <div className="flex justify-between items-center mb-1">
                     <span className="font-bold text-sm text-[#00c27a]">Pro</span>
                     <span className="text-lg font-black tracking-tight text-white">14.99 €</span>
                  </div>
                  <p className="text-[11px] text-[#737373] group-hover:text-[#9a9a9a] mb-4">9-Agent Live Execution.</p>
                  <Button className="w-full h-10 bg-[#00c27a] text-black font-bold rounded-lg text-xs">Go Pro</Button>
               </div>
            </div>
            
            <div className="mt-8 pt-6 border-t border-white/5">
               <p className="text-[10px] text-center text-[#555] italic uppercase tracking-wider">Privacy Guaranteed</p>
            </div>
          </div>

          {/* PROFESSIONAL (Style Design 7) */}
          <div className="relative flex flex-col p-8 rounded-[20px] border border-white/30 overflow-hidden" style={{ background: 'linear-gradient(180deg, rgba(255, 255, 255, 0.05) 0%, rgba(255, 255, 255, 0) 50%), rgba(255, 255, 255, 0.015)' }}>
            <div className="absolute inset-0 h-[2px] opacity-70" style={{ background: 'linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.8) 50%, transparent)' }} />
            <div className="text-[11px] font-mono tracking-[0.2em] mb-3 text-[#9a9a9a]">DOCKER FIRST</div>
            <h3 className="text-3xl font-bold mb-6">Professional</h3>
            <div className="mb-10">
               <div className="text-4xl font-black text-white">29.00 €</div>
               <div className="text-xs text-[#737373]">/ month</div>
            </div>
            <div className="space-y-5 flex-1 mb-10">
               <div className="flex gap-3">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5" />
                  <p className="text-xs text-[#9a9a9a]">24/7 Cloud Autopilot</p>
               </div>
               <div className="flex gap-3">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5" />
                  <p className="text-xs text-[#9a9a9a]">XAI Reasoning Chat</p>
               </div>
               <div className="flex gap-3">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5" />
                  <p className="text-xs text-[#9a9a9a]">BYOC - Your Servers</p>
               </div>
            </div>
            <Button className="w-full py-7 rounded-full bg-white text-black font-bold">Purchase License</Button>
          </div>

          {/* INSTITUTIONAL (Style Design 7) */}
          <div className="relative flex flex-col p-8 rounded-[20px] border border-[#00c27a]/30 overflow-hidden" style={{ background: 'linear-gradient(180deg, rgba(0, 194, 122, 0.05) 0%, rgba(0, 194, 122, 0) 50%), rgba(255, 255, 255, 0.015)' }}>
            <div className="absolute inset-0 h-[2px] opacity-70" style={{ background: 'linear-gradient(90deg, transparent, #00c27a 50%, transparent)' }} />
            <div className="text-[11px] font-mono tracking-[0.2em] mb-3 text-[#00c27a]">CLOUD NATIVE</div>
            <h3 className="text-3xl font-bold mb-6">Institutional</h3>
            <div className="mb-10">
               <div className="text-4xl font-black text-white">Custom</div>
               <div className="text-xs text-[#737373]">Pricing</div>
            </div>
            <div className="space-y-5 flex-1 mb-10">
               <div className="flex gap-3">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5" />
                  <p className="text-xs text-[#9a9a9a]">Multi-Tenant Scaling</p>
               </div>
               <div className="flex gap-3">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5" />
                  <p className="text-xs text-[#9a9a9a]">RTS 22 WORM Logging</p>
               </div>
               <div className="flex gap-3">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5" />
                  <p className="text-xs text-[#9a9a9a]">Isolated Corp VPC</p>
               </div>
            </div>
            <Button className="w-full py-7 rounded-full bg-[#00c27a] text-black font-bold">Contact Sales</Button>
          </div>
        </div>
      </section>

    </div>
  );
}
