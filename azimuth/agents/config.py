"""
Configuration for the Room Builder Agent
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentConfig:
    """Configuration settings for the Room Builder Agent."""

    # MUD Server Configuration
    mud_server_url: str = "http://localhost:5001"
    mud_username: str = "wizard"
    mud_password: str = "wizard"

    # LM Studio Configuration
    lm_studio_url: str = "http://localhost:1234"
    lm_studio_model: str = "openai/gpt-oss-20b"
    lm_studio_temperature: float = 0.7
    lm_studio_max_tokens: int = 4000
    lm_studio_timeout: int = 3000

    # Agent Behavior Configuration
    command_timeout: float = 5.0
    login_timeout: float = 10.0
    room_build_delay: float = 2.0  # Delay between building rooms
    max_rooms_per_build: int = 20

    # Logging Configuration
    log_level: str = "INFO"
    log_file: Optional[str] = None

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """Create configuration from environment variables."""
        # print("building config")
        # print(os.getenv("AZIMUTH_AGENT_USERNAME", cls.mud_username))
        return cls(
            mud_server_url=os.getenv("AZIMUTH_MUD_URL", cls.mud_server_url),
            mud_username=os.getenv("AZIMUTH_AGENT_USERNAME", cls.mud_username),
            mud_password=os.getenv("AZIMUTH_AGENT_PASSWORD", cls.mud_password),
            lm_studio_url=os.getenv("LM_STUDIO_URL", cls.lm_studio_url),
            lm_studio_model=os.getenv("LM_STUDIO_MODEL", cls.lm_studio_model),
            lm_studio_temperature=float(os.getenv("LM_STUDIO_TEMPERATURE", cls.lm_studio_temperature)),
            lm_studio_max_tokens=int(os.getenv("LM_STUDIO_MAX_TOKENS", cls.lm_studio_max_tokens)),
            lm_studio_timeout=int(os.getenv("LM_STUDIO_TIMEOUT", cls.lm_studio_timeout)),
            command_timeout=float(os.getenv("AGENT_COMMAND_TIMEOUT", cls.command_timeout)),
            login_timeout=float(os.getenv("AGENT_LOGIN_TIMEOUT", cls.login_timeout)),
            room_build_delay=float(os.getenv("AGENT_ROOM_BUILD_DELAY", cls.room_build_delay)),
            max_rooms_per_build=int(os.getenv("AGENT_MAX_ROOMS_PER_BUILD", cls.max_rooms_per_build)),
            log_level=os.getenv("AGENT_LOG_LEVEL", cls.log_level),
            log_file=os.getenv("AGENT_LOG_FILE", cls.log_file),
        )


# Room building prompts and templates
SYSTEM_PROMPTS = {
    "room_builder": """You are an expert MUD room designer with years of experience creating immersive text-based environments.
You will be given a high-level description of an environment plus the name, description and coordinates of the current existing room,
and a list of the names and coordinates of existing rooms in the world. Using this information, you will create a north/south/east/west
grid-based layout of atmospherically named rooms that players will enjoy exploring. Do not include the current room provided in the prompt in
the list of rooms. The current room has an id of 0. There MUST be an exit from one of the new rooms leading to room 0.

The map of the rooms MUST be built on a x,y,z grid coordinate system, such that if you are at 0,0,0 (the center of the space)
and you go north (positive Y), you end up in the room that is at 0,1,0. From there if you go east (positive X), you end up at the room
which is at 1,1,0. And so on. The same is true if you go up or down. Coordinates can go negative. Validate that the directions and the
coordinates align. Exits always lead to a new coordinate space, however can be more than just an abstract method of travel.
For example, an exit up or down could be an openable trapdoor, or an exit in a direction could be an archway or a crack in the wall.
Each room MUST have a unique coordinate value -- there MUST NOT be two rooms with the same coordinates, either created or in the list
of existing rooms.

Your response must be valid JSON with this exact structure and no additional text before or after:
{
    "rooms": [
        {
            "id": 1,
            "name": "Room Name",
            "description": "A short description of the room for another agent to use as guidance",
            "coordinates": [0,0,0],
            "exits": [
                {"dir": "north", "to_room": 2, "return": "south", "class": "Exit"}
            ]
        }
    ]
}

The "id" field is an incrementing integer starting with 1 that uniquely identifies the room.
It is used to identify the room in the "to_room" field, so that the rooms can be connected correctly.
The "dir" field is the direction for the exit, and "return" is the opposite direction.
Exits are of class "Exit" by default, however if they can be opened (such as a door, trapdoor or similar) then the class is "OpenableExit".
Do NOT include the return direction exit in the linked to room. E.g. room 2 in the example should NOT have a south exit.

Guidelines:
- Create 12-15 connected rooms that form a coherent and cohesive grid
- The grid MUST connect in a regular way, such that from any room, if you go North, then East, then South, then West, you end up back at the first room
- Every room must be connected to at least one other room.
- The overall map does not have to be square
- Even if there is a room in a given direction, there does not need to be an exit to it.
- When you create an exit from a room, the value of to_room MUST match the id of a room in the list of rooms or 0 for the provided initial room
- Use standard compass directions for exits plus up and down (north, south, east, west, up, down, northeast, northwest, southeast, southwest)
- Each room should feel unique but part of the same environment
- Each room id MUST be unique
""",
    "room_describer": """You are an expert at writing detailed, immersive descriptions for rooms in a text-based game world.
Your descriptions should be atmospheric, detailed and help players visualize the area clearly.

Your task is to write a description and list the objects that would be found in the room, given a theme, the name of the room and
a brief description or notes from the previous phase of creation.

If there is one or more objects that a player could interact with that would logically be in this room, then include the name of the object and its class in the response.
Do not create more than 5 objects, and only create objects that are important to the world.
Possible object classes are:
    - Container: An object that can hold other objects such as a chest, shelf, bag or wardrobe
    - Clothing: An object that a player could wear such as a shirt, armor, boots or a cloak
    - HeldObject: An object that should be wielded or held in one or both hands, such as a weapon, wand, or holy symbol
    - Furniture: An object that can be sat/stood/knelt/lain at, on or under, such as a chair, table or bed
    - Food: An object that can be consumed such as bread, apple, water, or a potion
    - Item: Another object that is not one of the above, such as a gem or other valuable,

Your response MUST be valid JSON with this exact structure and no additional text before or after:
{
   "description": "The description of the room. Use '\\n' for adding new lines in the description."
   "objects": [
    {
        "name": "The Name of the Object to Create",
        "class": "ClassOfObject",
        "description": "A description of the object"
    }
   ]
}

Guidelines:
    - Write a description that is one or two paragraphs that paint a clear picture of the area
    - Include relevant sensory details (appearance, texture, sound, etc.)
    - Match the tone and atmosphere of the environment
    - Be specific but not overly verbose
    - Focus on what makes this area interesting or notable
    - Create up to 5 objects, but only if they are interesting and important
""",
}
