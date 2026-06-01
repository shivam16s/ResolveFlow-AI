import urllib.request
import json
import traceback

def test_url(name, url):
    print(f"Testing {name}: {url}")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            status = response.getcode()
            body = response.read().decode('utf-8')
            try:
                data = json.loads(body)
                print(f"  [OK] {status} - JSON: {str(data)[:100]}")
                return True
            except json.JSONDecodeError:
                print(f"  [OK] {status} - Text: {body[:100]}")
                return True
    except urllib.error.HTTPError as e:
        print(f"  [FAIL] HTTP Error: {e.code} - {e.reason}")
        body = e.read().decode('utf-8')
        print(f"  Body: {body[:200]}")
    except Exception as e:
        print(f"  [FAIL] Exception: {e}")
    return False

print("=== BACKEND API TESTS ===")
test_url("Backend Health", "http://localhost:8000/api/health")

print("\n=== FRONTEND PROXY TESTS ===")
test_url("Frontend Health Proxy", "http://localhost:3000/api/health")
test_url("Frontend Rag Policies", "http://localhost:3000/api/rag/policies")
test_url("Frontend Setup Env", "http://localhost:3000/api/setup/env")

print("\n=== FRONTEND PAGE TESTS ===")
test_url("Frontend Homepage", "http://localhost:3000/")
