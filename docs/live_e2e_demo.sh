set -u
export AIOPS_AUTH=on OPERATOR_TOKENS=alice:op-secret VIEWER_TOKENS=guest:view-secret AGENT_TOKEN=agent-secret RETENTION_ENABLED=0 DATABASE_URL=postgresql://postgres:ira@localhost:5432/aiopsguard
pkill -f "uvicorn backend.main" ; pkill -f monitoring. ; sleep 1
python3 -m database.init_db --reset >/dev/null && python3 -m database.seed
python3 -m uvicorn backend.main:app --port 8000 > /tmp/api.log 2>&1 &
sleep 4
python3 -m monitoring.agent --server http://localhost:8000 --interval 2 --role api --hostname live-node > /tmp/agent.log 2>&1 &
sleep 12
python3 -m monitoring.simulate_overload --mode cpu --duration 150 > /tmp/sim.log 2>&1 &
H='Authorization: Bearer op-secret'
for i in $(seq 1 25); do
  sleep 3
  N=$(curl -s -m 15 -H "$H" localhost:8000/api/incidents | python3 -c "import json,sys;print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
  [ "$N" != "0" ] && break
done
echo "incidents found: $N after ~$((i*3))s"
sleep 6
IID=$(curl -s -m 15 -H "$H" localhost:8000/api/incidents | python3 -c "import json,sys;print(json.load(sys.stdin)[0]['incident_id'])")
curl -s -m 15 -H "$H" localhost:8000/api/incidents/$IID | python3 -c "
import json,sys;i=json.load(sys.stdin)
print('status:',i['status'],'| category:',i['category'],'| engine:',i['rca_source'],i['rca_model'],'| confidence:',i['confidence'])
print('root cause:',i['root_cause']);print('rca_error:',i['rca_error'])
print('why:',i['justification']);print('similar:',[(r['incident_id'],r['similarity']) for r in i['similar_refs']])
print('actions:',[(a['action_id'],a['target_name'],a['target_pid'],a['status'],a['risk_level']) for a in i['actions']])"
AID=$(curl -s -m 15 -H "$H" localhost:8000/api/incidents/$IID | python3 -c "import json,sys;a=json.load(sys.stdin)['actions'];print(a[0]['action_id'] if a else '')")
echo "--- viewer tries to approve:"; curl -s -m 15 -X POST -H 'Authorization: Bearer view-secret' -H 'content-type: application/json' -d '{"approve":true}' localhost:8000/api/actions/$AID/decision
echo; echo "--- operator approves:"; curl -s -m 15 -X POST -H "$H" -H 'content-type: application/json' -d '{"approve":true,"decided_by":"mallory"}' localhost:8000/api/actions/$AID/decision | python3 -c "import json,sys;print([(a['status'],a['decided_by']) for a in json.load(sys.stdin)['actions']])"
sleep 8
echo "--- agent log:"; grep "action #" /tmp/agent.log
echo "--- audit trail:"; curl -s -m 15 -H "$H" localhost:8000/api/audit/actions | python3 -c "import json,sys;[print(a['action_id'],a['status'],a['target_name'],'by',a['decided_by'],'|',a['result_message']) for a in json.load(sys.stdin)]"
sleep 25
echo "--- after kill, incident:"; curl -s -m 15 -H "$H" localhost:8000/api/incidents/$IID | python3 -c "import json,sys;i=json.load(sys.stdin);print(i['status'],i['resolved_at'])"
tail -2 /tmp/agent.log
echo "--- analytics 1h:"; curl -s -m 15 -H "$H" "localhost:8000/api/analytics/summary?hours=1" | python3 -c "import json,sys;a=json.load(sys.stdin);print(a['totals']);print(a['per_server'][0]['avg_cpu'],a['per_server'][0]['peak_cpu']);print(a['categories'])"
pkill -f uvicorn; pkill -f monitoring.; echo done