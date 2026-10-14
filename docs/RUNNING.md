# Running AIOpsGuard (Windows / PowerShell)

## 1. One-time setup
```powershell
# PostgreSQL must be installed. Create both databases:
psql -U postgres -c "CREATE DATABASE aiopsguard;"
psql -U postgres -c "CREATE DATABASE aiopsguard_test;"

python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # then edit DATABASE_URL (your postgres password)

python -m database.init_db    # creates tables, triggers, views
python -m database.seed       # optional: historical incidents for RCA context

cd frontend; npm install; cd ..
```

## 2. Start everything (3 terminals, project root)
```powershell
uvicorn backend.main:app --reload --port 8000     # API
python -m monitoring.agent --role api             # agent (sends data every 5s)
cd frontend; npm run dev                          # dashboard -> http://localhost:5173
```

## 3. Demo the full flow
```powershell
python -m monitoring.simulate_overload --mode cpu --duration 60
```
Watch: CPU rises -> ML flags anomaly -> incident appears -> RCA names the process ->
click **Approve** -> agent terminates it -> CPU recovers -> incident auto-resolves.
Other modes: `--mode memory --target 88`, `--mode logs`, `--mode all`.

The agent needs ~40 samples (about 3-4 minutes) before the Isolation Forest is trained;
until then only the hard thresholds apply. Let it run a few minutes before the demo.

## 4. LLM root-cause analysis
Put `ANTHROPIC_API_KEY=...` in `.env`. Without a key the rule-based RCA is used automatically
(the incident shows which engine produced the analysis). The LLM call is isolated in
`incidents/rca.py -> _call_llm()` so you can swap providers in one place.

## 5. Tests
```powershell
pytest -v
```
DB tests are skipped automatically if `aiopsguard_test` is unreachable.
