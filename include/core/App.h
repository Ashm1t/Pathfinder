#pragma once
#include <windows.h>

// Top-level application — owns the window, agent loop, and MCP clients.
class App
{
public:
    explicit App(HINSTANCE hInstance);
    ~App();

    bool Init();
    int  Run();

private:
    HINSTANCE m_hInstance;
};
