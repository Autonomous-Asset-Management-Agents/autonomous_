import React from "react";
import { Check, Shield, Server, Building, TrendingUp, Search, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";

const data = [
  {
    name: "Private Basic",
    description: "For beginners to explore risk-free.",
    price: "0 €",
    period: "/ forever",
    profitable: "Risk-free Paper Trading with 3-Agent Consensus.",
    auditable: "Transparent Local JSON-Audit Log.",
    safe: "100% Desktop Isolation. API keys stay on your machine.",
    cta: "Download for Free"
  },
  {
    name: "Private Pro",
    description: "For active traders moving to real market.",
    price: "14.99 €",
    period: "/ month",
    profitable: "Live market execution with full 9-Agent Consensus.",
    auditable: "Transparent Local JSON-Audit Log.",
    safe: "100% Desktop Isolation. API keys stay on your machine.",
    cta: "Upgrade to Pro",
    highlight: true
  },
  {
    name: "Professional",
    description: "24/7 automated trading in your own cloud.",
    price: "29 €",
    period: "/ month",
    extra: "+ Cloud hosting costs (e.g., AWS, Hetzner)",
    profitable: "24/7 Headless Autopilot execution.",
    auditable: "Explainable AI (XAI) Chat Interface.",
    safe: "BYOC (Bring Your Own Cloud). No SaaS risk.",
    cta: "Purchase License"
  },
  {
    name: "Institutional",
    description: "Secure setup for asset managers.",
    price: "Custom",
    period: "",
    profitable: "Multi-Tenant Architecture & Custom Plugins.",
    auditable: "WORM-Logging & MiFID II compliance.",
    safe: "Isolated Corporate VPC Deployment with Kill-Switch.",
    cta: "Contact Sales"
  }
];

export default function PricingDesignsEnglish() {
  return (
    <div className="min-h-screen bg-black text-white p-8 space-y-32 font-sans pb-32">
      
      {/* =========================================
          DESIGN 1: THE CLASSIC CLEAN
          ========================================= */}
      <section>
        <div className="text-center mb-16">
          <h2 className="text-3xl font-bold text-[#00c27a] mb-2 uppercase tracking-widest">Design 1: The Classic Clean</h2>
          <p className="text-[#9a9a9a]">Minimalist focus. Clear pricing. Legible list.</p>
        </div>

        <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          {data.map((tier) => (
            <div key={tier.name} className="bg-[#0b0b0b] border border-[#1a1a1a] rounded-xl p-8 flex flex-col transition-all hover:border-[#333]">
              <div className="mb-6">
                <h3 className="text-lg font-bold mb-1">{tier.name}</h3>
                <p className="text-xs text-[#9a9a9a]">{tier.description}</p>
              </div>
              <div className="mb-8">
                <span className="text-4xl font-extrabold">{tier.price}</span>
                <span className="text-[#9a9a9a] text-sm"> {tier.period}</span>
                {tier.extra && <div className="text-[10px] text-[#737373] mt-1">{tier.extra}</div>}
              </div>
              <div className="space-y-4 flex-1 mb-8">
                <div className="flex gap-3">
                  <TrendingUp className="w-4 h-4 text-[#00c27a] shrink-0" />
                  <div className="text-xs text-gray-300"><span className="text-white font-bold block mb-0.5">Profitable</span>{tier.profitable}</div>
                </div>
                <div className="flex gap-3">
                  <Search className="w-4 h-4 text-[#00c27a] shrink-0" />
                  <div className="text-xs text-gray-300"><span className="text-white font-bold block mb-0.5">Auditable</span>{tier.auditable}</div>
                </div>
                <div className="flex gap-3">
                  <Shield className="w-4 h-4 text-[#00c27a] shrink-0" />
                  <div className="text-xs text-gray-300"><span className="text-white font-bold block mb-0.5">Safe</span>{tier.safe}</div>
                </div>
              </div>
              <Button className={`w-full ${tier.highlight ? 'bg-[#00c27a] text-black hover:bg-green-500 font-bold' : 'bg-transparent border border-[#1a1a1a] text-white hover:border-[#333]'}`} variant={tier.highlight ? "default" : "outline"}>
                {tier.cta}
              </Button>
            </div>
          ))}
        </div>
      </section>

      {/* =========================================
          DESIGN 2: THE KEYWORD BLOCKS
          ========================================= */}
      <section>
        <div className="text-center mb-16">
          <h2 className="text-3xl font-bold text-[#00c27a] mb-2 uppercase tracking-widest">Design 2: The Keyword Blocks</h2>
          <p className="text-[#9a9a9a]">Bold visual distinction for the core value pillars.</p>
        </div>

        <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {data.map((tier) => (
            <div key={tier.name} className={`bg-[#0b0b0b] rounded-2xl p-6 flex flex-col border ${tier.highlight ? 'border-[#00c27a]' : 'border-transparent hover:border-[#1a1a1a]'}`}>
              <div className="flex justify-between items-start mb-4">
                <h3 className="text-xl font-bold">{tier.name}</h3>
                {tier.highlight && <Zap className="w-4 h-4 text-[#00c27a] fill-[#00c27a]" />}
              </div>
              <div className="mb-6 p-4 bg-[#111] rounded-lg">
                <span className="text-3xl font-black">{tier.price}</span>
                <span className="text-[#9a9a9a] text-xs"> {tier.period}</span>
                {tier.extra && <div className="text-[10px] text-[#737373] mt-1 italic">{tier.extra}</div>}
              </div>
              
              <div className="space-y-2 flex-1 mb-6">
                <div className="p-3 border border-[#1a1a1a] rounded-lg bg-black/40">
                  <div className="text-[10px] uppercase tracking-tighter text-[#00c27a] font-bold mb-1">Profitable</div>
                  <p className="text-[11px] text-[#9a9a9a] leading-relaxed">{tier.profitable}</p>
                </div>
                <div className="p-3 border border-[#1a1a1a] rounded-lg bg-black/40">
                  <div className="text-[10px] uppercase tracking-tighter text-[#00c27a] font-bold mb-1">Auditable</div>
                  <p className="text-[11px] text-[#9a9a9a] leading-relaxed">{tier.auditable}</p>
                </div>
                <div className="p-3 border border-[#1a1a1a] rounded-lg bg-black/40">
                  <div className="text-[10px] uppercase tracking-tighter text-[#00c27a] font-bold mb-1">Safe</div>
                  <p className="text-[11px] text-[#9a9a9a] leading-relaxed">{tier.safe}</p>
                </div>
              </div>

              <Button className={`w-full rounded-full font-bold ${tier.highlight ? 'bg-[#00c27a] text-black' : 'bg-white/5 text-white hover:bg-white/10'}`}>
                {tier.cta}
              </Button>
            </div>
          ))}
        </div>
      </section>

      {/* =========================================
          DESIGN 3: THE SPLIT CARD
          ========================================= */}
      <section>
        <div className="text-center mb-16">
          <h2 className="text-3xl font-bold text-[#00c27a] mb-2 uppercase tracking-widest">Design 3: The Split Card</h2>
          <p className="text-[#9a9a9a]">Maximum separation between pricing and the value promise.</p>
        </div>

        <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          {data.map((tier) => (
            <div key={tier.name} className="flex flex-col rounded-2xl overflow-hidden border border-[#1a1a1a] hover:scale-[1.02] transition-transform duration-300">
              {/* Header */}
              <div className="p-8 bg-[#111] border-b border-[#1a1a1a]">
                <h3 className="text-xl font-bold mb-2">{tier.name}</h3>
                <div className="flex items-baseline gap-1">
                  <span className="text-3xl font-bold text-white">{tier.price}</span>
                  <span className="text-xs text-[#9a9a9a]">{tier.period}</span>
                </div>
                {tier.extra && <div className="text-[10px] text-[#737373] mt-2 h-4">{tier.extra}</div>}
              </div>
              {/* Promise Section */}
              <div className="p-8 bg-black flex-1 flex flex-col">
                <div className="space-y-6 flex-1 mb-8">
                  <div className="relative pl-6 border-l border-[#333]">
                    <TrendingUp className="absolute -left-[9px] top-0 w-4 h-4 bg-black text-[#00c27a]" />
                    <p className="text-[11px] text-gray-400">{tier.profitable}</p>
                  </div>
                  <div className="relative pl-6 border-l border-[#333]">
                    <Search className="absolute -left-[9px] top-0 w-4 h-4 bg-black text-[#00c27a]" />
                    <p className="text-[11px] text-gray-400">{tier.auditable}</p>
                  </div>
                  <div className="relative pl-6 border-l border-[#333]">
                    <Shield className="absolute -left-[9px] top-0 w-4 h-4 bg-black text-[#00c27a]" />
                    <p className="text-[11px] text-gray-400 font-medium text-white">{tier.safe}</p>
                  </div>
                </div>
                <Button className={`w-full py-6 font-bold ${tier.highlight ? 'bg-[#00c27a] text-black' : 'bg-transparent border border-[#333] text-[#9a9a9a]'}`}>
                  {tier.cta}
                </Button>
              </div>
            </div>
          ))}
        </div>
      </section>

    </div>
  );
}
