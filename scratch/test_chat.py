import urllib.request
import urllib.parse
import sys

url = "http://localhost:8000/api/chat/message/stream?" + urllib.parse.urlencode({
    "customer_id": "CUST-1001",
    "message": "I was charged twice this month and want a refund"
})

print(f"Connecting to: {url}")
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as response:
        print(f"Status: {response.getcode()}")
        while True:
            line = response.readline()
            if not line:
                break
            print(f"Data: {line.decode('utf-8').strip()}")
except Exception as e:
    print(f"Error: {e}")
