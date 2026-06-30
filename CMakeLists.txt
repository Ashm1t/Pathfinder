cmake_minimum_required(VERSION 3.20)
project(Pathfinder VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Windows-only target
if(NOT WIN32)
  message(FATAL_ERROR "Pathfinder is a Windows-only application.")
endif()

# Sources
file(GLOB_RECURSE SOURCES
  "src/core/*.cpp"
  "src/hud/*.cpp"
  "src/agent/*.cpp"
  "src/mcp/*.cpp"
)

add_executable(Pathfinder WIN32 ${SOURCES} resources/pathfinder.rc)

target_include_directories(Pathfinder PRIVATE include third_party)

# Windows system libs
target_link_libraries(Pathfinder PRIVATE
  d2d1           # Direct2D rendering
  dwrite         # DirectWrite (text)
  dwmapi         # DWM (transparency, blur)
  uxtheme
  kernel32
  user32
  gdi32
  shell32
  ole32
  oleaut32
  uuid
)

# Compiler flags
target_compile_definitions(Pathfinder PRIVATE
  WIN32_LEAN_AND_MEAN
  NOMINMAX
  UNICODE
  _UNICODE
)
