import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { 
  pricingData, 
  sharedPricingStyles, 
  PricingTierData, 
  PricingFeature 
} from "@/lib/pricing-config";
import { IconBrandWindows, IconBrandGithub } from "@/console/shared/Icons";
import { platformDownload } from "@/lib/appVersion";

interface PricingCardProps {
  tier: PricingTierData | { Junior: PricingTierData; Senior: PricingTierData };
  mode?: string;
  setMode?: (mode: string) => void;
  isInstitutional?: boolean;
  isProfessional?: boolean;
}

export const PricingCard = ({ tier, mode, setMode, isInstitutional, isProfessional }: PricingCardProps) => {
  const isPrivate = !isInstitutional && !isProfessional;
  let data: PricingTierData;
  
  if (isPrivate) {
    const t = tier as { Junior: PricingTierData; Senior: PricingTierData };
    data = mode === "Junior" ? t.Junior : t.Senior;
  } else {
    data = tier as PricingTierData;
  }

  return (
    <article 
      className={`ed-tier ${isInstitutional ? 'ed-tier-ent' : isProfessional ? 'ed-tier-oss' : 'ed-tier-private'}`}
      style={{ 
        background: isInstitutional ? sharedPricingStyles.bgInstitutional : isProfessional ? sharedPricingStyles.bgProfessional : 'rgba(255, 255, 255, 0.015)',
        boxShadow: isInstitutional 
          ? '0 0 40px rgba(0, 194, 122, 0.12)' 
          : isProfessional 
            ? '0 0 40px rgba(255, 255, 255, 0.08)' 
            : '0 0 40px rgba(255, 255, 255, 0.05)',
        borderRadius: '18px',
        padding: '48px 40px',
        display: 'flex',
        flexDirection: 'column',
        position: 'relative',
        overflow: 'hidden'
      }}
    >
      {(isProfessional || isInstitutional) && (
        <div className="absolute inset-x-0 top-0 h-[2px] opacity-70" style={{ background: isInstitutional ? sharedPricingStyles.glowInstitutional : sharedPricingStyles.glowProfessional }} />
      )}
      
      <header className="ed-tier-head" style={{ marginBottom: '32px', borderBottom: '1px solid rgba(255, 255, 255, 0.08)', paddingBottom: '32px', height: sharedPricingStyles.headerHeight, display: 'flex', flexDirection: 'column' }}>
        <span className="ed-tier-eyebrow" style={{ 
          fontFamily: 'var(--lb-mono)', fontSize: '13px', letterSpacing: '2px', fontWeight: 600, textTransform: 'uppercase', color: isInstitutional ? '#00c27a' : 'rgba(255, 255, 255, 0.55)', display: 'block', marginBottom: '12px', whiteSpace: 'nowrap'
        }}>{data.eyebrow}</span>
        <h3 className="ed-tier-name" style={{ fontSize: '33px', fontWeight: 700, margin: 0, marginBottom: '12px' }}>{data.name}</h3>
        <p className="ed-tier-tag" style={{ fontSize: '16px', lineHeight: '1.6', color: 'rgba(255, 255, 255, 0.62)', margin: 0, flex: 1 }}>
          {data.tag}
        </p>
        
        {isPrivate && setMode && (
          <div className="flex p-1 bg-white/5 rounded-full w-fit mt-8 border border-white/5">
            <button
              onClick={() => setMode("Junior")}
              className={`px-6 py-2 rounded-full text-xs font-bold transition-all ${mode === "Junior" ? "bg-white text-black" : "text-[#555] hover:text-[#9a9a9a]"}`}
            >
              Junior
            </button>
            <button
              onClick={() => setMode("Senior")}
              className={`px-6 py-2 rounded-full text-xs font-bold transition-all ${mode === "Senior" ? "bg-white text-black" : "text-[#555] hover:text-[#9a9a9a]"}`}
            >
              Senior
            </button>
          </div>
        )}
      </header>

      <div className="ed-tier-body" style={{ display: 'flex', flexDirection: 'column', gap: '32px', flexGrow: 1 }}>
        <div>
           <div className="flex flex-col justify-start mb-6" style={{ height: sharedPricingStyles.priceContainerHeight }}>
              <div className="flex items-baseline gap-2 mb-1">
                <span className="text-4xl font-black text-white">{data.price}</span>
                {data.priceSub && (
                   <span className="text-xs text-[#555]">{data.priceSub}</span>
                )}
              </div>
              {data.priceMeta && (
                <div className={`text-[11px] ${isInstitutional ? 'text-[#00c27a] tracking-widest uppercase font-bold' : 'text-[#444] italic tracking-wide'}`}>
                  {data.priceMeta}
                </div>
              )}
           </div>

           {/* Quick Download Button (Option 1 & 2 Preview) */}
           <div className="hidden sm:block mb-8">
             {isInstitutional ? (
               <Button 
                 className="w-full py-6 rounded-full font-bold text-sm transition-all bg-[#00c27a] text-black hover:bg-[#00e08d] shadow-[0_0_20px_rgba(0,194,122,0.3)]"
                 onClick={() => {
                   if (window.Leadbooster) {
                     window.Leadbooster.open();
                   } else {
                     window.location.href = 'mailto:hello@aaagents.com';
                   }
                 }}
               >
                 Contact Us
               </Button>
             ) : isProfessional ? (
               <a 
                 href="https://github.com/Autonomous-Asset-Management-Agents/autonomous_"
                 style={{ textDecoration: 'none' }}
                 target="_blank"
                 rel="noopener noreferrer"
               >
                 <Button className="w-full py-6 rounded-full font-bold text-sm transition-all bg-[rgba(255,255,255,0.45)] text-black hover:bg-[rgba(255,255,255,0.55)] flex items-center justify-center gap-2">
                   <IconBrandGithub className="w-5 h-5" />
                   Download on GitHub
                 </Button>
               </a>
             ) : (
        <a href={platformDownload().url} style={{ textDecoration: 'none' }}>
          <Button className="w-full py-6 rounded-full font-bold text-sm transition-all bg-white text-black hover:bg-gray-200 flex items-center justify-center gap-2 relative overflow-hidden">
            {platformDownload().os === "windows" && <IconBrandWindows className="w-5 h-5" />}
            {platformDownload().label}
            <span className="ml-1 text-[11px] font-bold text-[#00c27a] flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 bg-[#00c27a] rounded-full shadow-[0_0_8px_#00c27a]" style={{ animation: 'pulse 2s infinite' }}></span>
              BETA
            </span>
          </Button>
        </a>
             )}
           </div>

           <ul className="ed-feat-list" style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '18px' }}>
              {data.features.map((f: PricingFeature, i: number) => (
                <li key={i} style={{ position: 'relative', paddingLeft: '22px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                   <div style={{ position: 'absolute', left: 0, top: '8px', width: '8px', height: '8px', background: '#00c27a', borderRadius: '50%', boxShadow: '0 0 0 4px rgba(0, 194, 122, 0.1)' }} />
                   <strong style={{ color: '#fff', fontWeight: 700, fontSize: '15px' }}>{f.title}</strong>
                   <span style={{ color: 'rgba(255, 255, 255, 0.62)', fontSize: '14px', lineHeight: '1.55' }}>{f.desc}</span>
                </li>
              ))}
           </ul>
        </div>
      </div>

      {isInstitutional ? (
        <Button 
          className="mt-12 w-full py-8 rounded-full font-bold text-base transition-all bg-[#00c27a] text-black hover:bg-[#00e08d] shadow-[0_0_32px_rgba(0,194,122,0.4)]"
          onClick={() => {
            if (window.Leadbooster) {
              window.Leadbooster.open();
            } else {
              window.location.href = 'mailto:hello@aaagents.com';
            }
          }}
        >
          Contact Us
        </Button>
      ) : isProfessional ? (
        <a 
          href="https://github.com/Autonomous-Asset-Management-Agents/autonomous_"
          style={{ textDecoration: 'none' }}
          target="_blank"
          rel="noopener noreferrer"
        >
          <Button className="mt-12 w-full py-8 rounded-full font-bold text-base transition-all bg-[rgba(255,255,255,0.45)] text-black hover:bg-[rgba(255,255,255,0.55)] flex items-center justify-center gap-2">
            <IconBrandGithub className="w-5 h-5" />
            Download on GitHub
          </Button>
        </a>
      ) : (
        <a href={platformDownload().url} style={{ textDecoration: 'none' }}>
          <Button className="mt-12 w-full py-8 rounded-full font-bold text-base transition-all bg-white text-black hover:bg-gray-200 flex items-center justify-center gap-2 relative overflow-hidden">
            {platformDownload().os === "windows" && <IconBrandWindows className="w-5 h-5" />}
            {platformDownload().label}
            <span className="ml-2 text-[12px] font-bold text-[#00c27a] flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 bg-[#00c27a] rounded-full shadow-[0_0_8px_#00c27a]" style={{ animation: 'pulse 2s infinite' }}></span>
              BETA
            </span>
          </Button>
        </a>
      )}
    </article>
  );
};

export default function PricingMaster() {
  const [mode, setMode] = useState("Junior");

  return (
    <div className="landing-d-root" style={{ background: '#000', color: '#fff', minHeight: '100vh', paddingBottom: '200px' }}>
      
      <section className="tr-section tr-section-editions dark" style={{ padding: '120px 0' }}>
        <div className="lb-container">
          
          <div className="text-center mb-16">
             <h2 className="tr-headline" style={{ 
               marginBottom: "12px", 
               textAlign: "center",
               fontSize: '48px',
               fontWeight: 800,
               letterSpacing: '-0.02em'
             }}>
               Editions<span style={{ color: '#fff' }}>.</span>
             </h2>
          </div>

          <div className="ed-tiers" style={{ 
            display: 'grid', 
            gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', 
            gap: '24px' 
          }}>
            <PricingCard tier={pricingData.private} mode={mode} setMode={setMode} />
            <PricingCard tier={pricingData.professional} isProfessional />
            <PricingCard tier={pricingData.institutional} isInstitutional />
          </div>
        </div>
      </section>

    </div>
  );
}
