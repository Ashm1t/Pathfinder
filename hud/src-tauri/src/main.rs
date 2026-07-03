// Pathfinder HUD shell.
//
// Thin Rust layer: owns the transparent/click-through/always-on-top window
// (see docs/UI_SPEC.md section 0) and bridges data from the Python agent's
// localhost IPC (agent/pathfinder_agent/ipc.py) into the webview via Tauri
// events. All panel rendering, animation, and layout lives in ui/ (HTML/CSS/JS).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::Value;
use std::time::Duration;
use tauri::{AppHandle, Emitter};

const AGENT_BASE_URL: &str = "http://127.0.0.1:8765";
const POLL_INTERVAL: Duration = Duration::from_secs(3);

#[tauri::command]
fn set_click_through(window: tauri::WebviewWindow, ignore: bool) -> Result<(), String> {
    window
        .set_ignore_cursor_events(ignore)
        .map_err(|e| e.to_string())
}

/// Case ids contain spaces ("FIR 214-26") — percent-encode path segments.
fn enc(segment: &str) -> String {
    segment.replace('%', "%25").replace(' ', "%20").replace('#', "%23")
}

#[tauri::command]
async fn fetch_chronology(case_id: String) -> Result<Value, String> {
    let url = format!("{AGENT_BASE_URL}/panels/chronology/{}", enc(&case_id));
    reqwest::Client::new()
        .get(url)
        .timeout(Duration::from_secs(3))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json::<Value>()
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
async fn fetch_workflows() -> Result<Value, String> {
    reqwest::Client::new()
        .get(format!("{AGENT_BASE_URL}/workflows"))
        .timeout(Duration::from_secs(3))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json::<Value>()
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
async fn run_workflow(workflow_id: String, case_id: String) -> Result<Value, String> {
    // Workflows can run LLM steps — allow minutes, not seconds.
    reqwest::Client::new()
        .post(format!("{AGENT_BASE_URL}/workflow/{}/run", enc(&workflow_id)))
        .query(&[("case_id", case_id)])
        .timeout(Duration::from_secs(180))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json::<Value>()
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
async fn author_workflow(request: String, register: bool) -> Result<Value, String> {
    reqwest::Client::new()
        .post(format!("{AGENT_BASE_URL}/workflow/author"))
        .json(&serde_json::json!({ "request": request, "register": register }))
        .timeout(Duration::from_secs(240))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json::<Value>()
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
async fn register_workflow(workflow: Value) -> Result<Value, String> {
    reqwest::Client::new()
        .post(format!("{AGENT_BASE_URL}/workflow/register"))
        .json(&serde_json::json!({ "workflow": workflow }))
        .timeout(Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json::<Value>()
        .await
        .map_err(|e| e.to_string())
}

async fn poll_agent_once(app: &AppHandle, client: &reqwest::Client) {
    match client
        .get(format!("{AGENT_BASE_URL}/panels"))
        .timeout(Duration::from_secs(2))
        .send()
        .await
    {
        Ok(resp) => match resp.json::<Value>().await {
            Ok(body) => {
                let _ = app.emit("panels-update", body);
                let _ = app.emit("agent-status", "connected");
            }
            Err(e) => eprintln!("[hud] bad /panels response: {e}"),
        },
        Err(e) => {
            eprintln!("[hud] agent unreachable: {e}");
            let _ = app.emit("agent-status", "disconnected");
        }
    }

    if let Ok(resp) = client
        .get(format!("{AGENT_BASE_URL}/notifications"))
        .timeout(Duration::from_secs(2))
        .send()
        .await
    {
        if let Ok(body) = resp.json::<Value>().await {
            let _ = app.emit("notifications-update", body);
        }
    }

    if let Ok(resp) = client
        .get(format!("{AGENT_BASE_URL}/health"))
        .timeout(Duration::from_secs(2))
        .send()
        .await
    {
        if let Ok(body) = resp.json::<Value>().await {
            let _ = app.emit("health-update", body);
        }
    }
}

fn spawn_agent_poller(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        let client = reqwest::Client::new();
        loop {
            poll_agent_once(&app, &client).await;
            tokio::time::sleep(POLL_INTERVAL).await;
        }
    });
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            set_click_through, fetch_chronology, fetch_workflows,
            run_workflow, author_workflow, register_workflow
        ])
        .setup(|app| {
            spawn_agent_poller(app.handle().clone());
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Pathfinder HUD");
}
