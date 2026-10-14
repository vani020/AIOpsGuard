import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { timeOf } from "../api";

export default function MetricsChart({ metrics }) {
  if (!metrics.length) return <p className="empty">Waiting for telemetry. Start the agent: <code>python -m monitoring.agent</code></p>;
  const data = metrics.map((m) => ({ t: timeOf(m.ts), CPU: m.cpu_percent, Memory: m.memory_percent, Disk: m.disk_percent }));
  return (
    <div style={{ height: 240 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 12, left: -16, bottom: 0 }}>
          <CartesianGrid stroke="#dde3ea" strokeDasharray="3 3" />
          <XAxis dataKey="t" tick={{ fontSize: 11 }} minTickGap={40} />
          <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" />
          <Tooltip />
          <Legend />
          <Line type="monotone" dataKey="CPU" stroke="#1f5fbf" dot={false} strokeWidth={2} isAnimationActive={false} />
          <Line type="monotone" dataKey="Memory" stroke="#c2410c" dot={false} strokeWidth={2} isAnimationActive={false} />
          <Line type="monotone" dataKey="Disk" stroke="#6b7a8c" dot={false} strokeDasharray="4 3" isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
