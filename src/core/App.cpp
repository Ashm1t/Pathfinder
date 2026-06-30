#include "core/App.h"

App::App(HINSTANCE hInstance)
    : m_hInstance(hInstance)
{}

App::~App() {}

bool App::Init()
{
    // TODO: create HUD overlay window
    // TODO: start agent watcher thread
    // TODO: connect MCP clients
    return true;
}

int App::Run()
{
    MSG msg{};
    while (GetMessageW(&msg, nullptr, 0, 0))
    {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return static_cast<int>(msg.wParam);
}
