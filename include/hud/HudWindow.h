#pragma once
#include <windows.h>
#include <d2d1.h>

// Transparent, frameless, always-on-top overlay window.
// Panels (RecentCases, MajorUpdates, Chronology, WhatsNext) are
// rendered as Direct2D layers inside this window.
class HudWindow
{
public:
    bool Create(HINSTANCE hInstance);
    void Show();
    void Hide();
    void Render();

private:
    HWND                        m_hwnd     = nullptr;
    ID2D1Factory*               m_d2dFactory = nullptr;
    ID2D1HwndRenderTarget*      m_renderTarget = nullptr;

    static LRESULT CALLBACK WndProc(HWND, UINT, WPARAM, LPARAM);
};
