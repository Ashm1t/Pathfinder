"""Pathfinder Agent — local, background intelligence layer for police case work.

The agent watches case folders, extracts structured facts (cheaply via folder
conventions, then via a local LLM), stores them in a private SQLite memory, and
exposes four panels (Recent Cases, Major Updates, Chronology, What's Next) for
the native HUD to read over a localhost boundary.
"""

__version__ = "0.1.0"
