export AIOPS_AUTH=on OPERATOR_TOKENS=alice:op-secret VIEWER_TOKENS=guest:view-secret AGENT_TOKEN=agent-secret RETENTION_ENABLED=0 DATABASE_URL=postgresql://postgres:ira@localhost:5432/aiopsguard PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
cd /home/claude/work/AIOpsGuard
pkill -f "uvicorn backend.main"; pkill -f simulate_fleet; pkill -f vite; sleep 1
python3 -m database.init_db --reset >/dev/null && python3 -m database.seed >/dev/null
python3 -m uvicorn backend.main:app --port 8000 > /tmp/api.log 2>&1 &
sleep 4
(cd frontend && node node_modules/vite/bin/vite.js --port 5173 > /tmp/vite.log 2>&1 &)
python3 -m monitoring.simulate_fleet --after 25 > /tmp/fleet.log 2>&1 &
sleep 6
timeout 150 python3 /tmp/ui_e2e.py
pkill -f uvicorn; pkill -f simulate_fleet; pkill -f vite; echo finished