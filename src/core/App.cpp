#include "core/App.h"
#include <iostream>

namespace Pathfinder {

App::App(HINSTANCE hInstance) : m_hInstance(hInstance) {}

App::~App() {
    if (m_agent) m_agent->stop();
}

bool App::Init() {
    m_cfg   = Config::load("config/pathfinder.json");
    m_agent = std::make_unique<AgentLoop>(m_cfg);

    if (!m_agent->start()) {
        MessageBoxW(nullptr,
                    L"Failed to start Pathfinder agent.\n"
                    L"Ensure Ollama is running and filesystem MCP is installed.",
                    L"Pathfinder — Startup Error",
                    MB_OK | MB_ICONERROR);
        return false;
    }

    // Register panel change callback (UI hook — replace with HUD redraw later)
    m_agent->panel_store().on_change([] {
        // TODO: signal HUD window to redraw
    });

    return true;
}

int App::Run() {
    MSG msg{};
    while (GetMessageW(&msg, nullptr, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return static_cast<int>(msg.wParam);
}

}  // namespace Pathfinder
