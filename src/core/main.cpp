#include <windows.h>
#include "core/App.h"

int WINAPI wWinMain(HINSTANCE hInstance, HINSTANCE, LPWSTR, int nCmdShow)
{
    App app(hInstance);
    if (!app.Init())
        return 1;

    return app.Run();
}
