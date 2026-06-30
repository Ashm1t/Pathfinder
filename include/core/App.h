#pragma once
#include "agent/AgentLoop.h"
#include "config/Config.h"
#include <windows.h>
#include <memory>

namespace Pathfinder {

class App {
public:
    explicit App(HINSTANCE hInstance);
    ~App();

    bool Init();
    int  Run();

private:
    HINSTANCE                    m_hInstance;
    Config                       m_cfg;
    std::unique_ptr<AgentLoop>   m_agent;
};

}  // namespace Pathfinder
