set -u
export AIOPS_AUTH=on OPERATOR_TOKENS=alice:op-secret VIEWER_TOKENS=guest:view-secret AGENT_TOKEN=agent-secret RETENTION_ENABLED=0 DATABASE_URL=postgresql://postgres:ira@localhost:5432/aiopsguard
pkill -f "uvicorn backend.main"; pkill -f simulate_fleet; sleep 1
python3 -m database.init_db --reset >/dev/null && python3 -m database.seed >/dev/null
python3 -m uvicorn backend.main:app --port 8000 > /tmp/api.log 2>&1 &
sleep 4
python3 -m monitoring.simulate_fleet --after 25 > /tmp/fleet.log 2>&1 &
H='Authorization: Bearer op-secret'
echo "waiting for failure..."; for i in $(seq 1 60); do sleep 3; grep -q CRITICAL /tmp/fleet.log && break; done
sleep 8
curl -s -m 15 -H "$H" localhost:8000/api/incidents | python3 -c "
import json,sys
for i in json.load(sys.stdin)[:4]: print('incident',i['incident_id'],i['hostname'],i['severity'],i['status'],'pending:',i['pending_actions'])"
IID=$(curl -s -m 15 -H "$H" localhost:8000/api/incidents | python3 -c "import json,sys;print([i for i in json.load(sys.stdin) if i['severity']=='critical' and i['hostname']=='server-1'][0]['incident_id'])")
curl -s -m 15 -H "$H" localhost:8000/api/incidents/$IID | python3 -c "
import json,sys;i=json.load(sys.stdin)
for a in i['actions']: print(' action',a['action_id'],a['action_type'],a['status'],'|',a['reason'])"
echo "--- routes before:"; curl -s -m 15 -H "$H" localhost:8000/api/routes | python3 -c "import json,sys;[print(' ',r['service_name'],'->',r['active_hostname']) for r in json.load(sys.stdin)['routes']]"
AID=$(curl -s -m 15 -H "$H" localhost:8000/api/incidents/$IID | python3 -c "import json,sys;print([a['action_id'] for a in json.load(sys.stdin)['actions'] if a['action_type']=='migrate_traffic'][0])")
echo "--- approve failover:"; curl -s -m 15 -X POST -H "$H" -H 'content-type: application/json' -d '{"approve":true}' localhost:8000/api/actions/$AID/decision | python3 -c "import json,sys;[print(' ',a['status'],a['decided_by'],a['result_message']) for a in json.load(sys.stdin)['actions'] if a['action_type']=='migrate_traffic']"
echo "--- routes after:"; curl -s -m 15 -H "$H" localhost:8000/api/routes | python3 -c "import json,sys;d=json.load(sys.stdin);[print(' ',r['service_name'],'->',r['active_hostname'],'(failed over)' if r['failed_over'] else '') for r in d['routes']];print(' event:',d['events'][0]['kind'],d['events'][0]['from_hostname'],'->',d['events'][0]['to_hostname'],'by',d['events'][0]['performed_by'])"
pkill -f uvicorn; pkill -f simulate_fleet; echo done