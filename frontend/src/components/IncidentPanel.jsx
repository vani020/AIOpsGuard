import { useState } from "react";
import { api, timeOf } from "../api";

export function IncidentList({ incidents, selectedId, onSelect }) {
  if (!incidents.length) return <p className="empty">No incidents. Run <code>python -m monitoring.simulate_overload --mode cpu</code> to create one.</p>;
  return (
    <ul className="incidents">
      {incidents.map((i) => (
        <li key={i.incident_id}>
          <button className={selectedId === i.incident_id ? "selected" : ""} onClick={() => onSelect(i.incident_id)}>
            <span className={`pill sev-${i.severity}`}>{i.severity}</span>
            <strong>{i.hostname}</strong>
            <span className="title">{i.title}</span>
            <small>{i.status.replace("_", " ")} · {timeOf(i.detected_at)}{i.pending_actions > 0 ? ` · ${i.pending_actions} awaiting approval` : ""}</small>
          </button>
        </li>
      ))}
    </ul>
  );
}

export function IncidentDetail({ incident, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!incident) return <p className="empty">Select an incident to see its root-cause analysis.</p>;

  const run = async (fn) => {
    setBusy(true); setError("");
    try { await fn(); await onChanged(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const reasons = [...new Set((incident.predictions || []).flatMap((p) => p.reasons))];
  const active = !["resolved", "closed"].includes(incident.status);

  return (
    <div className="detail">
      <header>
        <h3>#{incident.incident_id} · {incident.title}</h3>
        <span className={`pill sev-${incident.severity}`}>{incident.severity}</span>
      </header>
      <p className="meta">{incident.hostname} · {incident.status.replace("_", " ")}
        {incident.rca_source && ` · analysed by ${incident.rca_source === "llm" ? "LLM" : "rule engine"}`}</p>

      {incident.root_cause ? (
        <>
          <h4>Root cause</h4><p>{incident.root_cause}</p>
          <h4>Explanation</h4><p>{incident.explanation}</p>
          <h4>Recommendation</h4><p className="pre">{incident.recommendation}</p>
        </>
      ) : <p className="empty">Root-cause analysis is running…</p>}

      {reasons.length > 0 && (<><h4>Detection signals</h4><ul className="signals">{reasons.slice(0, 6).map((r) => <li key={r}>{r}</li>)}</ul></>)}

      {incident.actions.length > 0 && (
        <>
          <h4>Proposed actions</h4>
          {incident.actions.map((a) => (
            <div className="action" key={a.action_id}>
              <div>
                <strong>Terminate {a.target_name} (PID {a.target_pid})</strong>
                <small>{a.reason}</small>
                {a.result_message && <small>Result: {a.result_message}</small>}
              </div>
              {a.status === "pending" ? (
                <div className="btns">
                  <button className="danger" disabled={busy} onClick={() => run(() => api.decide(a.action_id, true))}>Approve</button>
                  <button disabled={busy} onClick={() => run(() => api.decide(a.action_id, false))}>Reject</button>
                </div>
              ) : <span className={`pill act-${a.status}`}>{a.status}</span>}
            </div>
          ))}
        </>
      )}

      <div className="btns footer">
        <button disabled={busy} onClick={() => run(() => api.analyze(incident.incident_id))}>Re-run analysis</button>
        {active && <button disabled={busy} onClick={() => run(() => api.setStatus(incident.incident_id, "resolved"))}>Mark resolved</button>}
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
