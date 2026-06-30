cmake_minimum_required(VERSION 3.20)
project(Pathfinder VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT WIN32)
  message(FATAL_ERROR "Pathfinder is a Windows-only application.")
endif()

# ── Third-party via FetchContent ─────────────────────────────────────────────
include(FetchContent)

FetchContent_Declare(nlohmann_json
  URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.hpp
  DOWNLOAD_NO_EXTRACT TRUE
  SOURCE_DIR ${CMAKE_BINARY_DIR}/include/nlohmann
)
FetchContent_MakeAvailable(nlohmann_json)

FetchContent_Declare(sqlite3
  URL https://www.sqlite.org/2024/sqlite-amalgamation-3460100.zip
  SOURCE_DIR ${CMAKE_BINARY_DIR}/_sqlite3_src
)
FetchContent_MakeAvailable(sqlite3)

# SQLite3 as a static library
add_library(sqlite3_lib STATIC
  ${CMAKE_BINARY_DIR}/_sqlite3_src/sqlite-amalgamation-3460100/sqlite3.c
)
target_include_directories(sqlite3_lib PUBLIC
  ${CMAKE_BINARY_DIR}/_sqlite3_src/sqlite-amalgamation-3460100
)
target_compile_definitions(sqlite3_lib PUBLIC
  SQLITE_THREADSAFE=1
  SQLITE_DEFAULT_WAL_SYNCHRONOUS=1
)

# ── Sources ──────────────────────────────────────────────────────────────────
set(BACKEND_SOURCES
  src/core/main.cpp
  src/core/App.cpp
  src/config/Config.cpp
  src/llm/OllamaAdapter.cpp
  src/mcp/McpClient.cpp
  src/mcp/FilesystemMcp.cpp
  src/memory/AgentMemory.cpp
  src/pipeline/DocumentPipeline.cpp
  src/pipeline/CaseExtractor.cpp
  src/workflow/WorkflowEngine.cpp
  src/agent/AgentLoop.cpp
  src/agent/PanelDataStore.cpp
)

add_executable(Pathfinder WIN32 ${BACKEND_SOURCES} resources/pathfinder.rc)

# ── Include paths ─────────────────────────────────────────────────────────────
target_include_directories(Pathfinder PRIVATE
  include
  third_party
  ${CMAKE_BINARY_DIR}/include         # nlohmann/json.hpp
  ${CMAKE_BINARY_DIR}/_sqlite3_src/sqlite-amalgamation-3460100
)

# ── Link libraries ───────────────────────────────────────────────────────────
target_link_libraries(Pathfinder PRIVATE
  sqlite3_lib
  # Windows system
  d2d1 dwrite dwmapi uxtheme winhttp
  kernel32 user32 gdi32 shell32
  ole32 oleaut32 uuid
)

# ── Compiler flags ────────────────────────────────────────────────────────────
target_compile_definitions(Pathfinder PRIVATE
  WIN32_LEAN_AND_MEAN
  NOMINMAX
  UNICODE
  _UNICODE
)

# ── Copy config to build dir ──────────────────────────────────────────────────
add_custom_command(TARGET Pathfinder POST_BUILD
  COMMAND ${CMAKE_COMMAND} -E copy_directory
    ${CMAKE_SOURCE_DIR}/config
    $<TARGET_FILE_DIR:Pathfinder>/config
  COMMENT "Copying config files to build directory"
)
