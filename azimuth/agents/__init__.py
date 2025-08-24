"""
Azimuth AI Agents Package

This package contains AI agents that can interact with the Azimuth MUD server
to perform various automated tasks like room building, NPC management, and
world generation.

Available agents:
- RoomBuilderAgent: Builds rooms and environments using LLM assistance
"""

from .room_builder import RoomBuilderAgent
from .config import AgentConfig, SYSTEM_PROMPTS

__version__ = "0.1.0"
__author__ = "Azimuth Development Team"

__all__ = [
    "RoomBuilderAgent",
    "AgentConfig",
    "SYSTEM_PROMPTS",
]
