/** #4874: agent profile details under round-table row */
import type { AgentProfile as Profile, AgentSetting } from "@/lib/api";

const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div><span className="text-white/60">{label}: </span>{children}</div>
);

export function AgentProfile({ agent }: { agent: AgentSetting }) {
  const p = agent.profile as Profile | undefined;
  if (!p?.type) return null;
  return (
    <details data-testid={`agent-profile-${agent.name}`} className="mt-2">
      <summary className="text-[11px] text-white/50 cursor-pointer">Profile</summary>
      <div className="text-[11.5px] text-white/45 leading-relaxed mt-1.5 space-y-1">
        <Field label="Type">{p.type}. {p.repeatable}</Field>
        <Field label="Role in the flow">{p.flow_role}</Field>
        <Field label="Responsibility">{p.responsibility}</Field>
        <Field label="Method">{p.methods}</Field>
        <Field label="Data">{p.data}</Field>
        <Field label="Settings">
          <ul className="list-disc pl-4">
            {p.settings.map((s) => (
              <li key={s.key}><span className="num break-all">{s.key}</span> = <span className="num">{s.default}</span>: {s.effect}</li>
            ))}
          </ul>
        </Field>
        <Field label="Default">{p.default_state}</Field>
        <Field label="Edition">{p.edition}</Field>
        <Field label="Missing data">{p.missing_data}</Field>
        <Field label="KPI">{p.kpi}</Field>
        <Field label="Measured">
          <ul className="list-disc pl-4">{p.measured.map((m) => <li key={m}>{m}</li>)}</ul>
        </Field>
        {p.literature.length > 0 && (
          <Field label="Literature">
            <ul className="list-disc pl-4">{p.literature.map((l) => <li key={l}>{l}</li>)}</ul>
          </Field>
        )}
        <Field label="Engine ID (logs)"><span className="num">{agent.name}</span></Field>
        <Field label="Evidence"><span className="num break-all">{p.evidence.join(" · ")}</span></Field>
      </div>
    </details>
  );
}
