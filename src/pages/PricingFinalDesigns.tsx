import React from "react";
import { Check, Shield, Server, Building, TrendingUp, Search, Zap, Globe, Cpu } from "lucide-react";
import { Button } from "@/components/ui/button";

const data = [
  {
    tier: "DESKTOP VERSION",
    name: "Private Basic",
    description: "The easiest way to get started with autonomous trading. Designed for individuals looking to discover AI-driven strategies risk-free.",
    price: "0.00 €",
    period: "/ forever",
    profitable: { title: "Paper Trading Ready", desc: "Built-in 3-agent consensus for safe, zero-risk strategy testing with simulated funds." },
    auditable: { title: "Local Audit Logging", desc: "Every decision is recorded in a manipulation-proof SHA-256 JSON log on your machine." },
    safe: { title: "100% Local & Private", desc: "Your keys and strategies never leave your machine. Maximum privacy for your personal portfolio." },
    cta: "Download for Windows",
    secondaryCta: "Coming soon: Mac"
  },
  {
    tier: "DESKTOP VERSION",
    name: "Private Pro",
    description: "Take your trading to the next level with real-market execution and the full power of the consensus framework.",
    price: "14.99 €",
    period: "/ month",
    profitable: { title: "9-Agent Consensus", desc: "Unlock the full Round Table V2 engine for live signal generation and execution." },
    auditable: { title: "Real-Time Transparency", desc: "Follow every trade reasoning string in real-time via the local audit console." },
    safe: { title: "OS Keychain Security", desc: "API credentials are encrypted via your operating system's native secure storage." },
    cta: "Upgrade to Pro",
    highlight: true
  },
  {
    tier: "DOCKER FIRST ARCHITECTURE",
    name: "Professional",
    description: "Transform your trading into a 24/7 operation. Fully containerized and optimized for private cloud environments.",
    price: "29.00 €",
    period: "/ month",
    extra: "+ Cloud hosting costs (e.g., AWS, Hetzner)",
    profitable: { title: "24/7 Headless Autopilot", desc: "Runs consistently in the cloud. Schläft nie – executes strategies while you sleep." },
    auditable: { title: "XAI Chat Interface", desc: "Query your trading engine via chat to understand the 'Why' behind every market move." },
    safe: { title: "Bring Your Own Cloud (BYOC)", desc: "Total infrastructure control. You host the engine on your own servers. Zero SaaS risk." },
    cta: "Purchase License"
  },
  {
    tier: "CLOUD NATIVE ARCHITECTURE",
    name: "Institutional",
    description: "The autonomous core as a fully audited corporate deployment. Delivering complete transparency for professional management.",
    price: "Custom",
    period: "Pricing",
    profitable: { title: "Multi-Tenant Scaling", desc: "Manage multiple portfolios and accounts through a single, high-performance instance." },
    auditable: { title: "Tamper-Proof WORM Audit", desc: "Compliance-ready logging with 8-year retention and RTS 22 automated export APIs." },
    safe: { title: "Isolated Corporate VPC", desc: "Hardened perimeter deployment with system-kill-switch and advanced risk controls." },
    cta: "Contact Us"
  }
];

export default function PricingFinalDesigns() {
  return (
    <div className="min-h-screen bg-black text-white p-8 space-y-40 font-sans pb-40" style={{ backgroundColor: '#000' }}>
      
      {/* =========================================
          DESIGN 4: THE REFINED MATRIX (GRID)
          ========================================= */}
      <section className="max-w-6xl mx-auto">
        <div className="text-center mb-16">
          <h1 className="text-5xl font-black mb-4">Editions.</h1>
          <p className="text-[#9a9a9a] uppercase tracking-[0.2em] text-sm">Design 4: The Refined Matrix</p>
        </div>

        <div className="grid grid-cols-5 border border-[#1a1a1a] rounded-3xl overflow-hidden bg-[#0b0b0b]">
          {/* Sidebar */}
          <div className="col-span-1 border-r border-[#1a1a1a] p-8 flex flex-col justify-end gap-24">
             <div className="space-y-24 mb-12">
                <div className="flex items-center gap-2 text-[#00c27a] font-bold"><TrendingUp size={18}/> Profitable</div>
                <div className="flex items-center gap-2 text-[#00c27a] font-bold"><Search size={18}/> Auditable</div>
                <div className="flex items-center gap-2 text-[#00c27a] font-bold"><Shield size={18}/> Safe</div>
             </div>
          </div>

          {/* Columns */}
          {data.map((item, i) => (
            <div key={i} className={`col-span-1 p-8 flex flex-col ${i < 3 ? 'border-r border-[#1a1a1a]' : ''}`}>
               <div className="mb-12">
                  <div className="text-[10px] text-[#9a9a9a] font-mono mb-2">{item.tier.split(' ')[0]}</div>
                  <div className="font-bold text-lg mb-1">{item.name}</div>
                  <div className="text-2xl font-black text-[#00c27a]">{item.price}</div>
               </div>
               
               <div className="space-y-12 flex-1">
                  <div className="text-xs text-[#9a9a9a] leading-relaxed">{item.profitable.desc}</div>
                  <div className="text-xs text-[#9a9a9a] leading-relaxed">{item.auditable.desc}</div>
                  <div className="text-xs text-[#9a9a9a] leading-relaxed">{item.safe.desc}</div>
               </div>
               
               <Button className="mt-12 w-full bg-white text-black font-bold text-xs rounded-full hover:bg-gray-200">
                  Select
               </Button>
            </div>
          ))}
        </div>
      </section>

      {/* =========================================
          DESIGN 5: THE SCREENSHOT REPLICA (1+2)
          ========================================= */}
      <section className="max-w-5xl mx-auto">
        <div className="text-center mb-16">
          <h1 className="text-6xl font-bold mb-12">Editions.</h1>
        </div>

        <div className="space-y-6">
          {/* Top Row: Private Edition (Container for Basic & Pro) */}
          <div className="bg-[#0b0b0b] border border-[#1a1a1a] rounded-[32px] p-12 grid grid-cols-1 md:grid-cols-2 gap-12">
            <div>
              <div className="text-[10px] text-[#9a9a9a] font-mono mb-4 tracking-widest">DESKTOP VERSION</div>
              <h2 className="text-4xl font-bold mb-6">Private Edition</h2>
              <p className="text-[#9a9a9a] text-lg leading-relaxed mb-8">
                The easiest way to get started with <span className="text-white font-bold underline decoration-[#00c27a]">autonomous_</span> trading. 
                Runs as a completely local installation.
              </p>
              <div className="flex gap-4">
                <Button className="bg-white text-black rounded-full px-8 py-6 font-bold flex items-center gap-2 hover:bg-gray-200">
                  Download Free (0.00 €)
                </Button>
                <Button className="bg-[#333] text-white rounded-full px-8 py-6 font-bold hover:bg-[#444]">
                  Go Pro (14.99 €)
                </Button>
              </div>
            </div>
            <div className="space-y-8">
              <div className="flex gap-4">
                <div className="w-2 h-2 rounded-full bg-[#00c27a] mt-2 shrink-0"></div>
                <div>
                  <div className="font-bold text-white mb-1">Profitable Execution</div>
                  <div className="text-sm text-[#9a9a9a]">Choose between 3 agents (Basic) or full 9-agent consensus (Pro).</div>
                </div>
              </div>
              <div className="flex gap-4">
                <div className="w-2 h-2 rounded-full bg-[#00c27a] mt-2 shrink-0"></div>
                <div>
                  <div className="font-bold text-white mb-1">Local Audit & Privacy</div>
                  <div className="text-sm text-[#9a9a9a]">100% private. Audit logs and API keys remain strictly on your own computer.</div>
                </div>
              </div>
              <div className="flex gap-4">
                <div className="w-2 h-2 rounded-full bg-[#00c27a] mt-2 shrink-0"></div>
                <div>
                  <div className="font-bold text-white mb-1">Safe & Reliable</div>
                  <div className="text-sm text-[#9a9a9a]">Zero configuration. Lightweight Electron app with local SQLite database.</div>
                </div>
              </div>
            </div>
          </div>

          {/* Bottom Row: Professional & Institutional */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {data.slice(2).map((item, i) => (
              <div key={i} className={`bg-[#0b0b0b] border ${i === 1 ? 'border-[#00c27a]/30' : 'border-[#1a1a1a]'} rounded-[32px] p-10 flex flex-col`}>
                <div className={`text-[10px] ${i === 1 ? 'text-[#00c27a]' : 'text-[#9a9a9a]'} font-mono mb-4 tracking-widest uppercase`}>{item.tier}</div>
                <h3 className="text-3xl font-bold mb-4">{item.name}</h3>
                <p className="text-[#9a9a9a] text-sm leading-relaxed mb-8 flex-1">{item.description}</p>
                
                <div className="mb-10 pb-8 border-b border-[#1a1a1a]">
                   <div className="flex items-baseline gap-2 mb-1">
                      <span className="text-4xl font-black">{item.price}</span>
                      <span className="text-[#9a9a9a] text-sm">{item.period}</span>
                   </div>
                   {item.extra && <div className="text-[10px] text-[#737373] italic">{item.extra}</div>}
                </div>

                <div className="space-y-6 mb-12">
                  <div className="flex gap-3">
                    <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5 shrink-0"></div>
                    <div>
                      <div className="text-xs font-bold text-white mb-0.5">Profitable: {item.profitable.title}</div>
                      <div className="text-[11px] text-[#9a9a9a]">{item.profitable.desc}</div>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5 shrink-0"></div>
                    <div>
                      <div className="text-xs font-bold text-white mb-0.5">Auditable: {item.auditable.title}</div>
                      <div className="text-[11px] text-[#9a9a9a]">{item.auditable.desc}</div>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5 shrink-0"></div>
                    <div>
                      <div className="text-xs font-bold text-white mb-0.5">Safe: {item.safe.title}</div>
                      <div className="text-[11px] text-[#9a9a9a]">{item.safe.desc}</div>
                    </div>
                  </div>
                </div>

                <Button className={`w-full py-7 rounded-full font-bold shadow-lg ${i === 1 ? 'bg-[#00c27a] text-black' : 'bg-white text-black'}`}>
                   {item.cta}
                </Button>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* =========================================
          DESIGN 6: THE BENTO GRID (4 CARDS)
          ========================================= */}
      <section className="max-w-7xl mx-auto px-4">
        <div className="flex justify-between items-end mb-16 border-b border-[#1a1a1a] pb-8">
           <div>
              <h1 className="text-5xl font-bold mb-2">Editions.</h1>
              <p className="text-[#9a9a9a]">Profitable. Auditable. Safe.</p>
           </div>
           <div className="text-right">
              <div className="text-[10px] text-[#00c27a] font-mono tracking-widest mb-1">CURRENCY</div>
              <div className="text-2xl font-bold uppercase">EUR / USD</div>
           </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {data.map((item, i) => (
            <div key={i} className="bg-[#0b0b0b] border border-[#1a1a1a] rounded-2xl p-6 flex flex-col group hover:border-[#00c27a]/50 transition-all">
              <div className="h-1.5 w-12 bg-[#1a1a1a] rounded-full mb-6 group-hover:bg-[#00c27a] transition-colors"></div>
              <div className="text-[9px] text-[#9a9a9a] font-mono mb-1">{item.tier}</div>
              <h3 className="text-xl font-bold mb-4">{item.name}</h3>
              
              <div className="mb-6">
                 <div className="text-3xl font-black text-white">{item.price}</div>
                 <div className="text-[10px] text-[#9a9a9a]">{item.period}</div>
              </div>

              <div className="space-y-4 flex-1 mb-8">
                 <div className="p-3 rounded-xl bg-black border border-[#1a1a1a]">
                    <div className="text-[10px] font-bold text-[#00c27a] mb-1">PROFITABLE</div>
                    <p className="text-[11px] text-[#9a9a9a]">{item.profitable.desc}</p>
                 </div>
                 <div className="p-3 rounded-xl bg-black border border-[#1a1a1a]">
                    <div className="text-[10px] font-bold text-[#00c27a] mb-1">AUDITABLE</div>
                    <p className="text-[11px] text-[#9a9a9a]">{item.auditable.desc}</p>
                 </div>
                 <div className="p-3 rounded-xl bg-black border border-[#1a1a1a]">
                    <div className="text-[10px] font-bold text-[#00c27a] mb-1">SAFE</div>
                    <p className="text-[11px] text-[#9a9a9a]">{item.safe.desc}</p>
                 </div>
              </div>

              <Button className="w-full bg-white text-black font-bold rounded-xl py-6 hover:bg-[#00c27a] hover:text-black transition-colors">
                 {item.cta.split(' ')[0]}
              </Button>
            </div>
          ))}
        </div>
      </section>

      {/* =========================================
          DESIGN 7: THE SOVEREIGN FOUR (FINAL)
          Incorporating specific border glow effects per tier.
          ========================================= */}
      <section className="max-w-7xl mx-auto px-4">
        <div className="text-center mb-24">
          <h2 className="text-6xl font-black mb-6">Sovereign Editions.</h2>
          <p className="text-[#9a9a9a] uppercase tracking-[0.3em] text-sm">Design 7: The Authentic Edge-Glow Layout</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          {data.map((item, i) => {
            const isInstitutional = item.name === "Institutional";
            const isProfessional = item.name === "Professional";
            const isPro = item.name === "Private Pro";
            
            // Border Colors
            let borderColor = "rgba(255, 255, 255, 0.1)";
            if (isPro) borderColor = "rgba(255, 255, 255, 0.2)";
            if (isProfessional) borderColor = "rgba(255, 255, 255, 0.32)";
            if (isInstitutional) borderColor = "rgba(0, 194, 122, 0.32)";

            // Background
            let bgStyle = "rgba(255, 255, 255, 0.015)";
            if (isProfessional) bgStyle = "linear-gradient(180deg, rgba(255, 255, 255, 0.05) 0%, rgba(255, 255, 255, 0) 50%), rgba(255, 255, 255, 0.015)";
            if (isInstitutional) bgStyle = "linear-gradient(180deg, rgba(0, 194, 122, 0.05) 0%, rgba(0, 194, 122, 0) 50%), rgba(255, 255, 255, 0.015)";

            // Glow Line
            let glowLine = null;
            if (isProfessional) glowLine = "linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.8) 50%, transparent)";
            if (isInstitutional) glowLine = "linear-gradient(90deg, transparent, #00c27a 50%, transparent)";

            return (
              <div 
                key={i} 
                className="relative flex flex-col p-8 rounded-[20px] overflow-hidden transition-all duration-500"
                style={{ 
                  border: `1px solid ${borderColor}`,
                  background: bgStyle,
                  boxShadow: isInstitutional ? '0 20px 40px rgba(0,194,122,0.05)' : 'none'
                }}
              >
                {/* Glow Line ::before replica */}
                {glowLine && (
                  <div 
                    className="absolute inset-0 h-[2px] opacity-70"
                    style={{ background: glowLine }}
                  />
                )}

                <div className="mb-10">
                   <div className={`text-[11px] font-mono tracking-[0.2em] mb-3 ${isInstitutional ? 'text-[#00c27a]' : 'text-[#9a9a9a]'}`}>
                      {item.tier}
                   </div>
                   <h3 className="text-3xl font-bold mb-2">{item.name}</h3>
                   <div className="flex items-baseline gap-2">
                      <span className="text-2xl font-black text-white">{item.price}</span>
                      <span className="text-xs text-[#737373]">{item.period}</span>
                   </div>
                </div>

                <div className="space-y-8 flex-1 mb-12">
                   <div className="flex gap-4 group/item">
                      <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5 shrink-0 shadow-[0_0_8px_rgba(0,194,122,0.8)]" />
                      <div>
                        <div className="text-[10px] font-black tracking-widest text-[#555] mb-1 group-hover/item:text-[#00c27a] transition-colors">PROFITABLE</div>
                        <p className="text-[13px] text-[#9a9a9a] leading-relaxed">{item.profitable.desc}</p>
                      </div>
                   </div>
                   <div className="flex gap-4 group/item">
                      <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5 shrink-0 shadow-[0_0_8px_rgba(0,194,122,0.8)]" />
                      <div>
                        <div className="text-[10px] font-black tracking-widest text-[#555] mb-1 group-hover/item:text-[#00c27a] transition-colors">AUDITABLE</div>
                        <p className="text-[13px] text-[#9a9a9a] leading-relaxed">{item.auditable.desc}</p>
                      </div>
                   </div>
                   <div className="flex gap-4 group/item">
                      <div className="w-1.5 h-1.5 rounded-full bg-[#00c27a] mt-1.5 shrink-0 shadow-[0_0_8px_rgba(0,194,122,0.8)]" />
                      <div>
                        <div className="text-[10px] font-black tracking-widest text-[#555] mb-1 group-hover/item:text-[#00c27a] transition-colors">SAFE</div>
                        <p className="text-[13px] text-[#9a9a9a] leading-relaxed">{item.safe.desc}</p>
                      </div>
                   </div>
                </div>

                <Button 
                  className={`w-full py-7 rounded-full font-bold transition-all duration-300 ${
                    isInstitutional 
                    ? 'bg-[#00c27a] text-black hover:bg-[#00e08d] shadow-[0_0_24px_rgba(0,194,122,0.3)]' 
                    : 'bg-white text-black hover:bg-gray-200'
                  }`}
                >
                  {item.cta}
                </Button>
                
                {item.secondaryCta && (
                  <div className="mt-4 text-center text-[10px] text-[#555] hover:text-[#9a9a9a] cursor-pointer">
                    {item.secondaryCta}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>

    </div>
  );
}
