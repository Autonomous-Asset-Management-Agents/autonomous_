import React from "react";
import { Check, Shield, Server, Building, Activity, FileText, Lock, TrendingUp, Search } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function PricingVariantsPreview() {
  return (
    <div className="min-h-screen bg-black text-white p-8 space-y-32 font-sans">
      
      {/* =========================================
          VARIANT A: THE MATRIX 
          ========================================= */}
      <section>
        <div className="text-center mb-12">
          <h2 className="text-3xl font-bold text-[#00c27a] mb-2">Variante A: Die Matrix</h2>
          <p className="text-[#9a9a9a]">Fester Keyword-Fokus als Zeilen. Perfekt für direkten, rationalen Vergleich.</p>
        </div>

        <div className="max-w-6xl mx-auto overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-[#1a1a1a]">
                <th className="p-4 w-1/4"></th>
                <th className="p-4 w-1/4">
                  <div className="font-bold text-xl">Private Edition</div>
                  <div className="text-sm text-[#9a9a9a] font-normal">Desktop (Win/Mac)</div>
                </th>
                <th className="p-4 w-1/4">
                  <div className="font-bold text-xl">Professional</div>
                  <div className="text-sm text-[#9a9a9a] font-normal">Self-Hosted Cloud</div>
                </th>
                <th className="p-4 w-1/4">
                  <div className="font-bold text-xl">Institutional</div>
                  <div className="text-sm text-[#9a9a9a] font-normal">Corporate VPC</div>
                </th>
              </tr>
            </thead>
            <tbody>
              {/* PROFITABLE ROW */}
              <tr className="border-b border-[#1a1a1a] hover:bg-[#0b0b0b] transition-colors">
                <td className="p-4 font-bold text-lg flex items-center gap-2">
                  <TrendingUp className="text-[#00c27a] w-5 h-5" /> Profitable
                </td>
                <td className="p-4 text-[#9a9a9a]">
                  <span className="text-white font-medium">9-Agent Consensus.</span><br/>
                  Paper-Trading zum Lernen, Live-Trading (Echtgeld) für Rendite.
                </td>
                <td className="p-4 text-[#9a9a9a]">
                  <span className="text-white font-medium">24/7 Autopilot.</span><br/>
                  Headless Execution für lückenlose Marktpräsenz.
                </td>
                <td className="p-4 text-[#9a9a9a]">
                  <span className="text-white font-medium">Custom Plugins.</span><br/>
                  Komplexe Multi-Asset Portfolios.
                </td>
              </tr>
              {/* AUDITABLE ROW */}
              <tr className="border-b border-[#1a1a1a] hover:bg-[#0b0b0b] transition-colors">
                <td className="p-4 font-bold text-lg flex items-center gap-2">
                  <Search className="text-[#00c27a] w-5 h-5" /> Auditable
                </td>
                <td className="p-4 text-[#9a9a9a]">
                  <span className="text-white font-medium">Local JSON-Log.</span><br/>
                  Sekundengenaue Transparenz jeder KI-Entscheidung als Datei.
                </td>
                <td className="p-4 text-[#9a9a9a]">
                  <span className="text-white font-medium">XAI Chat Interface.</span><br/>
                  Frag das System per Chat, warum ein Trade ausgeführt wurde.
                </td>
                <td className="p-4 text-[#9a9a9a]">
                  <span className="text-white font-medium">WORM-Database.</span><br/>
                  Revisionssicheres Logging für höchste Compliance.
                </td>
              </tr>
              {/* SAFE ROW */}
              <tr className="border-b border-[#1a1a1a] hover:bg-[#0b0b0b] transition-colors">
                <td className="p-4 font-bold text-lg flex items-center gap-2">
                  <Shield className="text-[#00c27a] w-5 h-5" /> Safe
                </td>
                <td className="p-4 text-[#9a9a9a]">
                  <span className="text-white font-medium">100% Desktop Isolation.</span><br/>
                  API-Keys bleiben auf dem Rechner. Kein Datenabfluss.
                </td>
                <td className="p-4 text-[#9a9a9a]">
                  <span className="text-white font-medium">BYOC (Own Cloud).</span><br/>
                  Du hostest die Engine. Kein SaaS. Du behältst die Hoheit.
                </td>
                <td className="p-4 text-[#9a9a9a]">
                  <span className="text-white font-medium">Iron Dome Isolation.</span><br/>
                  Separater Kill-Switch im Corporate Netzwerk.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      {/* =========================================
          VARIANT B: PERSONA-BOXES
          ========================================= */}
      <section>
        <div className="text-center mb-12">
          <h2 className="text-3xl font-bold text-[#00c27a] mb-2">Variante B: Persona-Boxen</h2>
          <p className="text-[#9a9a9a]">Vertraute Kartenansicht, aber die Keywords sind die primären Features pro Box.</p>
        </div>

        <div className="max-w-6xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-6">
          
          {/* Card 1 */}
          <div className="bg-[#0b0b0b] border border-[#1a1a1a] rounded-xl p-8 flex flex-col">
            <h3 className="text-2xl font-bold mb-1">Private Edition</h3>
            <p className="text-[#9a9a9a] text-sm mb-6">Für Tech-Enthusiasten & Hobby-Trader</p>
            
            <div className="space-y-6 flex-1 mb-8">
              <div>
                <div className="flex items-center gap-2 font-bold mb-1"><TrendingUp className="w-4 h-4 text-[#00c27a]"/> Profitable</div>
                <p className="text-sm text-[#9a9a9a] pl-6">Nutze den 9-Agenten-Konsensus live oder lerne risikofrei im Paper-Trading.</p>
              </div>
              <div>
                <div className="flex items-center gap-2 font-bold mb-1"><Search className="w-4 h-4 text-[#00c27a]"/> Auditable</div>
                <p className="text-sm text-[#9a9a9a] pl-6">Jeder Trade wird lokal im manipulatonsfreien JSON-Log gespeichert.</p>
              </div>
              <div>
                <div className="flex items-center gap-2 font-bold mb-1"><Shield className="w-4 h-4 text-[#00c27a]"/> Safe</div>
                <p className="text-sm text-[#9a9a9a] pl-6">100% Desktop-Isolation. API-Keys im OS-Keychain. Kein SaaS.</p>
              </div>
            </div>
            <Button variant="outline" className="w-full border-[#1a1a1a] bg-transparent text-white hover:text-[#00c27a]">Kostenlos starten</Button>
          </div>

          {/* Card 2 */}
          <div className="bg-[#0b0b0b] border border-[#00c27a] rounded-xl p-8 flex flex-col relative shadow-[0_0_30px_rgba(0,194,122,0.1)]">
            <div className="absolute top-0 right-0 bg-[#00c27a] text-black text-xs font-bold px-3 py-1 rounded-bl-lg">24/7 AUTOPILOT</div>
            <h3 className="text-2xl font-bold mb-1">Professional</h3>
            <p className="text-[#9a9a9a] text-sm mb-6">Für Vermögensaufbau mit maximaler Effizienz</p>
            
            <div className="space-y-6 flex-1 mb-8">
              <div>
                <div className="flex items-center gap-2 font-bold mb-1"><TrendingUp className="w-4 h-4 text-[#00c27a]"/> Profitable</div>
                <p className="text-sm text-[#9a9a9a] pl-6">Headless Deployment für 24/7 Marktpräsenz ohne Emotionen.</p>
              </div>
              <div>
                <div className="flex items-center gap-2 font-bold mb-1"><Search className="w-4 h-4 text-[#00c27a]"/> Auditable</div>
                <p className="text-sm text-[#9a9a9a] pl-6">Erklärbare KI (XAI): Frag das System jederzeit per Chat, warum es gehandelt hat.</p>
              </div>
              <div>
                <div className="flex items-center gap-2 font-bold mb-1"><Shield className="w-4 h-4 text-[#00c27a]"/> Safe</div>
                <p className="text-sm text-[#9a9a9a] pl-6">Bring Your Own Cloud (BYOC). Du hostest. Wir lesen nicht mit.</p>
              </div>
            </div>
            <Button className="w-full bg-[#00c27a] text-black hover:bg-green-500 font-bold">Lizenz erwerben</Button>
          </div>

          {/* Card 3 */}
          <div className="bg-[#0b0b0b] border border-[#1a1a1a] rounded-xl p-8 flex flex-col">
            <h3 className="text-2xl font-bold mb-1">Institutional</h3>
            <p className="text-[#9a9a9a] text-sm mb-6">Für Family Offices & Wealth Manager</p>
            
            <div className="space-y-6 flex-1 mb-8">
              <div>
                <div className="flex items-center gap-2 font-bold mb-1"><TrendingUp className="w-4 h-4 text-[#00c27a]"/> Profitable</div>
                <p className="text-sm text-[#9a9a9a] pl-6">Multi-Tenant Architektur & Custom Plugins für komplexe Portfolios.</p>
              </div>
              <div>
                <div className="flex items-center gap-2 font-bold mb-1"><Search className="w-4 h-4 text-[#00c27a]"/> Auditable</div>
                <p className="text-sm text-[#9a9a9a] pl-6">WORM-Logging & MiFID II Ready für lückenlose Compliance.</p>
              </div>
              <div>
                <div className="flex items-center gap-2 font-bold mb-1"><Shield className="w-4 h-4 text-[#00c27a]"/> Safe</div>
                <p className="text-sm text-[#9a9a9a] pl-6">Isoliertes Corporate VPC Deployment mit System-Kill-Switch.</p>
              </div>
            </div>
            <Button variant="outline" className="w-full border-[#1a1a1a] bg-transparent text-white hover:text-[#00c27a]">Sales kontaktieren</Button>
          </div>

        </div>
      </section>

      {/* =========================================
          VARIANT C: THE STORYLINE
          ========================================= */}
      <section>
        <div className="text-center mb-12">
          <h2 className="text-3xl font-bold text-[#00c27a] mb-2">Variante C: Die Storyline</h2>
          <p className="text-[#9a9a9a]">Die Keywords sind die Hauptkapitel der Seite. Die Editionen sind die Lösungswege.</p>
        </div>

        <div className="max-w-4xl mx-auto space-y-12">
          
          {/* SAFE SECTION */}
          <div className="bg-[#0b0b0b] border-l-4 border-[#00c27a] p-8 rounded-r-xl">
            <div className="flex items-center gap-3 mb-4">
              <Shield className="w-8 h-8 text-[#00c27a]" />
              <h3 className="text-3xl font-bold">SAFE</h3>
            </div>
            <p className="text-lg text-white mb-6">Absolute Datensouveränität. Niemand liest mit.</p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="border border-[#1a1a1a] p-4 rounded-lg bg-black">
                <div className="font-bold text-[#00c27a] mb-1">Private</div>
                <div className="text-sm text-[#9a9a9a]">100% lokale Ausführung auf deinem Desktop.</div>
              </div>
              <div className="border border-[#1a1a1a] p-4 rounded-lg bg-black">
                <div className="font-bold text-[#00c27a] mb-1">Professional</div>
                <div className="text-sm text-[#9a9a9a]">Self-Hosted (BYOC). Deine Cloud, deine Regeln.</div>
              </div>
              <div className="border border-[#1a1a1a] p-4 rounded-lg bg-black">
                <div className="font-bold text-[#00c27a] mb-1">Institutional</div>
                <div className="text-sm text-[#9a9a9a]">Abgeschottet in deinem Corporate VPC.</div>
              </div>
            </div>
          </div>

          {/* AUDITABLE SECTION */}
          <div className="bg-[#0b0b0b] border-l-4 border-[#00c27a] p-8 rounded-r-xl">
            <div className="flex items-center gap-3 mb-4">
              <Search className="w-8 h-8 text-[#00c27a]" />
              <h3 className="text-3xl font-bold">AUDITABLE</h3>
            </div>
            <p className="text-lg text-white mb-6">Verstehe jede Entscheidung der KI.</p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="border border-[#1a1a1a] p-4 rounded-lg bg-black">
                <div className="font-bold text-[#00c27a] mb-1">Private</div>
                <div className="text-sm text-[#9a9a9a]">Transparente, lokale JSON-Audit-Logs.</div>
              </div>
              <div className="border border-[#1a1a1a] p-4 rounded-lg bg-black">
                <div className="font-bold text-[#00c27a] mb-1">Professional</div>
                <div className="text-sm text-[#9a9a9a]">XAI-Chat: Frag die Engine live, warum sie handelt.</div>
              </div>
              <div className="border border-[#1a1a1a] p-4 rounded-lg bg-black">
                <div className="font-bold text-[#00c27a] mb-1">Institutional</div>
                <div className="text-sm text-[#9a9a9a]">WORM-Datenbank für rechtssicheres Reporting.</div>
              </div>
            </div>
          </div>

          {/* PROFITABLE SECTION */}
          <div className="bg-[#0b0b0b] border-l-4 border-[#00c27a] p-8 rounded-r-xl">
            <div className="flex items-center gap-3 mb-4">
              <TrendingUp className="w-8 h-8 text-[#00c27a]" />
              <h3 className="text-3xl font-bold">PROFITABLE</h3>
            </div>
            <p className="text-lg text-white mb-6">Autonome Ausführung ohne Emotionen.</p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="border border-[#1a1a1a] p-4 rounded-lg bg-black">
                <div className="font-bold text-[#00c27a] mb-1">Private</div>
                <div className="text-sm text-[#9a9a9a]">Live-Trading mit 9 kooperierenden KI-Agenten.</div>
              </div>
              <div className="border border-[#1a1a1a] p-4 rounded-lg bg-black">
                <div className="font-bold text-[#00c27a] mb-1">Professional</div>
                <div className="text-sm text-[#9a9a9a]">24/7 Autopilot-Modus für lückenloses Trading.</div>
              </div>
              <div className="border border-[#1a1a1a] p-4 rounded-lg bg-black">
                <div className="font-bold text-[#00c27a] mb-1">Institutional</div>
                <div className="text-sm text-[#9a9a9a]">Multi-Tenant Skalierung & Custom Plugins.</div>
              </div>
            </div>
          </div>

        </div>
      </section>

    </div>
  );
}
