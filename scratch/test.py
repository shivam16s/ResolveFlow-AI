import sys
import json
from backend.agent.llm_client import LLMClient
from backend.agent.intent_classifier import IntentClassifier

llm = LLMClient()
prompt = IntentClassifier.build_prompt('check it again')
raw = llm(prompt)
print("RAW:")
print(repr(raw))
try:
    print(IntentClassifier._extract_json_object(raw))
except Exception as e:
    print("ERROR:", e)
