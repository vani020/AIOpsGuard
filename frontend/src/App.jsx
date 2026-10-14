import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import ServerCard from "./components/ServerCard";
import MetricsChart from "./components/MetricsChart";
import { LogsPanel, ProcessTable } from "./components/Panels";
import { IncidentDetail, IncidentList } from "./components/IncidentPanel";

export default function App() {
  const [servers, setServers] = useState([]);
  const [serverId, setServerId] = useState(null);
  const [metrics, setMetrics] = useState([]);
  const [processes, setProcesses] = useState([]);
  const [logs, setLogs] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [incidentId, setIncidentId] = useState(null);
  const [incident, setIncident] = useState(null);
  const [online, setOnline] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const [s, inc] = await Promise.all([api.servers(), api.incidents()]);
      setServers(s); setIncidents(inc); setOnline(true);
      const sid = serverId ?? s[0]?.server_id ?? null;
      if (serverId == null && sid != null) setServerId(sid);
      if (sid != null) {
        const [m, p, l] = await Promise.all([api.metrics(sid), api.processes(sid), api.logs(sid)]);
        setMetrics(m); setProcesses(p); setLogs(l);
      }
      if (incidentId != null) setIncident(await api.incident(incidentId));
    } catch { setOnline(false); }
  }, [serverId, incidentId]);

  useEffect(() => { refresh(); const t = setInterval(refresh, 3000); return () => clearInterval(t); }, [refresh]);

  const active = incidents.filter((i) => !["resolved", "closed"].includes(i.status));
  const awaiting = incidents.reduce((n, i) => n + (i.pending_actions || 0), 0);
  const critical = servers.filter((s) => s.display_status === "critical").length;

  return (
    <div className="app">
      <header className="top">
        <div>
          <h1>AIOpsGuard</h1>
          <p>Infrastructure monitoring, anomaly detection and incident response</p>
        </div>
        <dl className="summary">
          <div><dt>Servers</dt><dd>{servers.length}</dd></div>
          <div><dt>Critical</dt><dd className={critical ? "bad-text" : ""}>{critical}</dd></div>
          <div><dt>Active incidents</dt><dd className={active.length ? "bad-text" : ""}>{active.length}</dd></div>
          <div><dt>Awaiting approval</dt><dd>{awaiting}</dd></div>
        </dl>
        <span className={`live ${online ? "on" : "off"}`}>{online ? "Live" : "API offline"}</span>
      </header>

      <main>
        <section className="col">
          <h2>Servers</h2>
          <div className="servers">
            {servers.length === 0 && <p className="empty">No servers reporting yet. Start the monitoring agent.</p>}
            {servers.map((s) => <ServerCard key={s.server_id} server={s} selected={s.server_id === serverId} onSelect={setServerId} />)}
          </div>
          <h2>Resource usage</h2>
          <div className="card"><MetricsChart metrics={metrics} /></div>
          <h2>Top processes</h2>
          <div className="card"><ProcessTable processes={processes} /></div>
          <h2>Warnings and errors</h2>
          <div className="card"><LogsPanel logs={logs} /></div>
        </section>

        <aside className="col">
          <h2>Incidents</h2>
          <div className="card flush"><IncidentList incidents={incidents} selectedId={incidentId} onSelect={setIncidentId} /></div>
          <h2>Root-cause analysis</h2>
          <div className="card"><IncidentDetail incident={incident} onChanged={refresh} /></div>
        </aside>
      </main>
    </div>
  );
}
