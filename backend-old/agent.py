from typing import Dict, Any
import ollama
from prompts import SYSTEM_PROMPT
from rag import rag_engine

def process_command(command: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Process natural language commands using Ollama (Phi-3) with RAG.
    """
    try:
        # 1. Build Context String
        context_str = "No specific case selected."
        if context:
            context_str = f"Case ID: {context.get('id')}\nName: {context.get('name')}\nSuspects: {context.get('suspects')}\n"

        # 2. RAG Retrieval (Manual Integration)
        try:
            rag_results = rag_engine.query(command)
            if rag_results:
                context_str += "\n\nRelevant Documents from Knowledge Base:\n" 
                context_str += "\n".join([f"- {d.page_content[:500]}..." for d in rag_results])
        except Exception as e:
            print(f"RAG Error (continuing without RAG): {e}")

        # 3. Format Prompt
        formatted_system_prompt = SYSTEM_PROMPT.format(context=context_str)

        # 4. Call Ollama Directly
        response = ollama.chat(model='phi3', messages=[
            {'role': 'system', 'content': formatted_system_prompt},
            {'role': 'user', 'content': command},
        ])
        
        ai_response = response['message']['content']
        
        # Simple heuristic for actions (can be expanded later)
        action = "none"
        details = {}
        
        if "draft" in command.lower():
            action = "create_document"
            details = {"type": "Document", "status": "Drafted"}

        return {
            "response": ai_response,
            "action": action,
            "details": details
        }

    except Exception as e:
        print(f"Error calling Ollama: {e}")
        return {
            "response": f"I encountered an error connecting to my brain (Ollama). Please ensure the 'phi3' model is pulled and running. Error: {str(e)}",
            "action": "error",
            "details": {"error": str(e)}
        }
