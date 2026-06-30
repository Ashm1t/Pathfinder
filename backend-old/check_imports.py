import langchain.agents
print(f"langchain.agents contents: {dir(langchain.agents)}")

try:
    from langchain.agents import AgentExecutor
    print("AgentExecutor found in langchain.agents")
except ImportError as e:
    print(f"Error 1: {e}")

try:
    from langchain.agents import create_react_agent
    print("create_react_agent found in langchain.agents")
except ImportError as e:
    print(f"Error 2: {e}")

try:
    from langchain.agents import initialize_agent
    print("initialize_agent found in langchain.agents")
except ImportError as e:
    print(f"Error 3: {e}")
