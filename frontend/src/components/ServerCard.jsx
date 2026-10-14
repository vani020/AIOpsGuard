import { pct } from "../api";

function Bar({ label, value, warnAt }) {
  const v = value ?? 0;
  const tone = v >= warnAt + 5 ? "bad" : v >= warnAt ? "warn" : "ok";
  return (
    <div className="bar-row">
      <span>{label}</span>
      <div className="bar"><i className={tone} style={{ width: `${Math.min(v, 100)}%` }} /></div>
      <b>{pct(value)}</b>
    </div>
  );
}

export default function ServerCard({ server, selected, onSelect }) {
  const status = server.display_status;
  return (
    <button className={`server ${selected ? "selected" : ""}`} onClick={() => onSelect(server.server_id)}>
      <div className="server-head">
        <strong>{server.hostname}</strong>
        <span className={`pill ${status}`}>{status}</span>
      </div>
      <small>{server.role} · {server.os_name || "unknown OS"}</small>
      <Bar label="CPU" value={server.cpu_percent} warnAt={80} />
      <Bar label="Memory" value={server.memory_percent} warnAt={75} />
      <Bar label="Disk" value={server.disk_percent} warnAt={85} />
      <div className="risk">
        <span>Failure risk</span>
        <b className={server.failure_risk >= 0.65 ? "bad-text" : ""}>{Math.round((server.failure_risk || 0) * 100)}%</b>
        {server.open_incidents > 0 && <em>{server.open_incidents} open incident</em>}
      </div>
    </button>
  );
}
