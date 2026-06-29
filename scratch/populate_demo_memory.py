import asyncio
import json
import os
import sqlite3
from backend.agent.memory_manager import MemoryManager
from backend.agent.memory import MemoryUnit

async def main():
    manager = MemoryManager()
    
    def mock_llm(prompt: str) -> str:
        if 'Rahul Sharma has churn risk' in prompt:
            return json.dumps({"triples": [
                {'subject': 'Rahul Sharma', 'relation': 'has risk', 'object': 'churn', 'confidence': 0.9, 'evidence': 'has churn risk'},
                {'subject': 'churn', 'relation': 'tied to', 'object': 'outages', 'confidence': 0.8, 'evidence': 'tied to repeated outages'},
                {'subject': 'outages', 'relation': 'location', 'object': 'Chennai Zone-04', 'confidence': 0.9, 'evidence': 'Chennai Zone-04 outages'}
            ]})
        elif 'duplicate payments' in prompt:
            return json.dumps({"triples": [
                {'subject': 'Rahul', 'relation': 'had payment', 'object': 'PAY-1001-A', 'confidence': 0.9, 'evidence': 'had duplicate payments PAY-1001-A'},
                {'subject': 'Rahul', 'relation': 'had payment', 'object': 'PAY-1001-B', 'confidence': 0.9, 'evidence': 'and PAY-1001-B'},
                {'subject': 'PAY-1001-A', 'relation': 'for invoice', 'object': 'INV-8821', 'confidence': 0.9, 'evidence': 'for invoice INV-8821'}
            ]})
        else:
            return json.dumps({"triples": [
                {'subject': 'session', 'relation': 'status', 'object': 'waiting', 'confidence': 0.9, 'evidence': 'session is waiting'},
                {'subject': 'session', 'relation': 'waiting on', 'object': 'diagnostic verification', 'confidence': 0.8, 'evidence': 'waiting on one router diagnostic verification'}
            ]})
            
    manager.llm_client = mock_llm
    
    conn = sqlite3.connect('d:/Hackathon/ResolveFlow-AI/data/resolveflow.db')
    rows = conn.execute('SELECT memory_id, customer_id, content, session_id, memory_type FROM memory_store WHERE customer_id=\'CUST-1001\'').fetchall()
    
    units = []
    for row in rows:
        units.append(MemoryUnit(
            content=row[2], 
            memory_type=row[4], 
            topic="demo",
            source_role="system",
            source_turn_index=0,
            confidence=1.0,
            entity_tags=[]
        ))
    
    if not units:
        print("No units found! Cannot populate memory graph.")
        return
        
    # We already stored to chroma in the previous run, so let's just use the memory IDs
    memory_ids = [row[0] for row in rows]
    print('Using Memory IDs:', memory_ids)
    
    from backend.agent.memory_graph import update_memory_graph
    
    for unit, mem_id in zip(units, memory_ids):
        triples = manager._extract_triples(unit)
        print('Triples for', mem_id, triples)
        update_memory_graph(manager.graph_connection, customer_id='CUST-1001', memory_id=mem_id, triples=triples)
        
    print('Added synonymy edges:', manager._add_synonymy_edges('CUST-1001'))
    
asyncio.run(main())
