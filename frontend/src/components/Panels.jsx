import { pct, timeOf } from "../api";

export function ProcessTable({ processes }) {
  if (!processes.length) return <p className="empty">No process data yet.</p>;
  return (
    <table>
      <thead><tr><th>PID</th><th>Process</th><th>CPU</th><th>Memory</th><th>State</th></tr></thead>
      <tbody>
        {processes.map((p) => (
          <tr key={p.pid}>
            <td>{p.pid}</td><td>{p.name}</td><td>{pct(p.cpu_percent)}</td><td>{pct(p.memory_percent)}</td><td>{p.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function LogsPanel({ logs }) {
  if (!logs.length) return <p className="empty">No warnings or errors in the latest logs.</p>;
  return (
    <ul className="logs">
      {logs.map((l) => (
        <li key={l.log_id} className={l.level}>
          <time>{timeOf(l.ts)}</time><b>{l.level}</b><span>{l.message}</span>
        </li>
      ))}
    </ul>
  );
}
