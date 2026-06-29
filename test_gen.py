import asyncio
from fastapi import Request
from backend.api.chat_routes import chat_stream, ChatMessage
class MockApp: state = type('State', (), {'db_path': 'data/resolveflow.db', 'policy_dir': 'data/policies'})()
class MockRequest: app = MockApp()
async def test():
    try:
        async for chunk in chat_stream(ChatMessage(customer_id='CUST-1001', message='test'), MockRequest()):
            print(chunk)
    except Exception as e:
        import traceback; traceback.print_exc()
asyncio.run(test())
