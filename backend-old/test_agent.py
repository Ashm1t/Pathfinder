import sys
import os

# Add current directory to path
sys.path.append(os.getcwd())

try:
    from agent import process_command
    print("Successfully imported process_command")
    
    response = process_command("hello")
    print(f"Response: {response}")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
