#pragma once
#include <thread>
#include <atomic>

// Background watcher — polls file system, DB, and .gov portals via MCPs.
// On change: updates panel data and signals HUD to redraw.
class AgentLoop
{
public:
    void Start();
    void Stop();

private:
    void Tick();

    std::thread     m_thread;
    std::atomic_bool m_running{false};
};
