import httpx
import sys

base = 'http://localhost:8000/api/v1'
c = httpx.Client(base_url=base)

results = []

# 1. Dashboard Stats
r = c.get('/dashboard/stats')
results.append(('GET /dashboard/stats', r.status_code, r.text[:100]))

# 2. List Skills
r = c.get('/skills')
results.append(('GET /skills', r.status_code, r.text[:100]))

# 3. List Sessions (empty initially)
r = c.get('/sessions')
results.append(('GET /sessions', r.status_code, r.text[:100]))

# 4. Create a Session
r = c.post('/sessions', json={'skill_name': 'api_smoke_test'})
results.append(('POST /sessions', r.status_code, r.text[:200]))
session_id = r.json().get('id') if r.status_code == 200 else None

# 5. Get Session detail (if created)
if session_id:
    r = c.get(f'/sessions/{session_id}')
    results.append(('GET /sessions/{id}', r.status_code, r.text[:200]))

# 6. Get MCP Servers
r = c.get('/mcp/servers')
results.append(('GET /mcp/servers', r.status_code, r.text[:200]))

# 7. List Defects
r = c.get('/defects')
results.append(('GET /defects', r.status_code, r.text[:100]))

# 8. List RAG Collections
r = c.get('/rag')
results.append(('GET /rag', r.status_code, r.text[:100]))

# 9. Get Resource Usage
r = c.get('/resources')
results.append(('GET /resources', r.status_code, r.text[:100]))

print('=== API Endpoint Verification ===')
for name, status, body in results:
    ok = status in (200, 201)
    icon = '[OK]' if ok else '[FAIL]'
    print(f'{icon} {name} => {status}')
    if not ok:
        print(f'   Body: {body[:200]}')

print(f'\nSession ID created: {session_id}')

# If session was created, test the cancel
if session_id:
    r = c.post(f'/sessions/{session_id}/cancel')
    print(f'[{'OK' if r.status_code == 200 else 'FAIL'}] POST /sessions/{session_id}/cancel => {r.status_code}')
    if r.status_code != 200:
        print(f'   Body: {r.text[:200]}')

sys.exit(0 if all(s in (200, 201) for _, s, _ in results) else 1)
