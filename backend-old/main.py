from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
from analyzer import scan_directory

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_PATH = r"d:\Tunday Kebabi\Tempest"

@app.get("/")
def read_root():
    return {"Hello": "FIR Analyzer Backend"}

@app.get("/cases")
def get_cases():
    return scan_directory(BASE_PATH)

@app.get("/schedule")
def get_schedule_endpoint():
    from analyzer import get_schedule
    return get_schedule(BASE_PATH)

@app.post("/agent/chat")
async def agent_chat(request: dict):
    from agent import process_command
    command = request.get("message", "")
    return process_command(command)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
