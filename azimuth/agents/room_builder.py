#!/usr/bin/env python3
"""
AI Room Builder Agent

This agent connects to both the Azimuth MUD server and LM Studio to automatically
build rooms and describe them based on high-level instructions.

The agent can:
1. Communicate with LM Studio for LLM-powered room generation
2. Create rooms, exits, and objects based on natural language descriptions
3. Generate detailed room descriptions and populate them with appropriate objects
"""

import json
import logging
import requests
from typing import Dict, Optional
from .config import AgentConfig, SYSTEM_PROMPTS

from ..entities import Place, Exit, OpenableExit, Object, Furniture, Clothing, HeldObject, Container


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RoomBuilderAgent:
    def __init__(self, world, config: AgentConfig = None):
        """Initialize the Room Builder Agent."""
        self.config = config or AgentConfig.from_env()

        # Room building state
        self.building_in_progress = False
        self.built_rooms = {}  # Track built rooms and their IDs

        # Configure logging
        log_level = getattr(logging, self.config.log_level.upper(), logging.INFO)
        logging.basicConfig(
            level=log_level,
            filename=self.config.log_file,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        self.test_lm_studio_connection()

        self.object_class_hash = {
            "Container": Container,
            "HeldObject": HeldObject,
            "Clothing": Clothing,
            "Food": None,
            "Furniture": Furniture,
            "Item": Object,
        }

        self.world = world
        self.player = world.load(world.players["wizard"])
        start = self.player.last_location
        if not start:
            start = world.config["start_room_id"]
        sloc = world.load(start)
        if isinstance(sloc, Place):
            self.player.move_to(sloc)
        else:
            print("Could not move player into the world, as start/last_loc is not a Place")

    def test_lm_studio_connection(self) -> bool:
        """Test connection to LM Studio."""
        try:
            print(f"Testing connection to LM Studio at {self.config.lm_studio_url}")

            # Test the /v1/models endpoint
            response = requests.get(f"{self.config.lm_studio_url}/v1/models", timeout=5.0)

            if response.status_code == 200:
                models = response.json()
                if models.get("data"):
                    print("✅ LM Studio connection successful")
                    print(f"Available models: {len(models['data'])}")
                    return True
                else:
                    print("⚠️  LM Studio connected but no models loaded")
                    return False
            else:
                print(f"❌ LM Studio returned status {response.status_code}")
                return False

        except requests.exceptions.RequestException as e:
            print(f"❌ LM Studio connection failed: {e}")
            return False

    def query_llm(self, prompt: str, system_prompt: str = None) -> Optional[str]:
        """Query LM Studio for LLM response."""
        try:
            payload = {
                "model": self.config.lm_studio_model,
                "messages": [],
                "temperature": self.config.lm_studio_temperature,
                "max_tokens": self.config.lm_studio_max_tokens,
                "stream": False,
            }

            if system_prompt:
                payload["messages"].append({"role": "system", "content": system_prompt})
            payload["messages"].append({"role": "user", "content": prompt})

            response = requests.post(
                f"{self.config.lm_studio_url}/v1/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.config.lm_studio_timeout,
            )

            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"]
            else:
                logger.error(f"LLM request failed: {response.status_code} - {response.text}")
                return None

        except requests.exceptions.RequestException as e:
            logger.error(f"llm_unavailable: {e}")
            return None
        except Exception as e:
            logger.error(f"Error querying LLM: {e}")
            return None

    def response_to_json(self, response, which):
        try:
            # Extract JSON from response if it's wrapped in markdown
            if "```json" in response:
                json_start = response.find("```json") + 7
                json_end = response.find("```", json_start)
                response = response[json_start:json_end].strip()
            elif "```" in response:
                json_start = response.find("```") + 3
                json_end = response.find("```", json_start)
                response = response[json_start:json_end].strip()

            js = json.loads(response)
            # Validate the structure
            if which == "plan" and self._validate_plan_response(js):
                return js
            elif which == "room" and self._validate_room_response(js):
                return js
            else:
                logger.error(f"invalid_response from llm: {response}")
                return None
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.debug(f"Response was: {response}")
            return None

    def generate_room_plan(self, description: str, from_room) -> Optional[Dict]:
        """Generate a detailed room building plan using LLM."""
        system_prompt = SYSTEM_PROMPTS["room_builder"]
        start = from_room.name
        room_desc = from_room.description
        coords = repr(from_room.coordinates)[1:-1]

        all_rooms = self.world.get_all_objects(Place)
        all_room_strs = [f"{room.name} ({repr(room.coordinates)[1:-1]})" for room in all_rooms]
        room_list = "\n".join(sorted(all_room_strs))

        prompt = f"""
Generate a room plan for the following environment:
{description}
The current room 0 is a room called "{start}", has coordinates of ({coords}) and is described as:
{room_desc}

The list of other existing rooms and their coordinates:
{room_list}
        """

        print(prompt)
        response = self.query_llm(prompt, system_prompt)
        if response:
            js = self.response_to_json(response, "plan")
            return js
        return None

    def generate_room_description(self, room):
        """Generate full description plus object list for room"""
        system_prompt = SYSTEM_PROMPTS["room_describer"]
        prompt = f"""
Please provide the description and objects for the following room:
Name: {room.name}
Short Description: {room.description}
        """
        response = self.query_llm(prompt, system_prompt)
        if response:
            js = self.response_to_json(response, "room")
            return js
        return None

    def _validate_plan_response(self, plan: Dict) -> bool:
        """Validate that a room plan has the correct structure."""
        if not isinstance(plan, dict):
            return False

        if "rooms" not in plan or not isinstance(plan["rooms"], list):
            return False

        for room in plan["rooms"]:
            if not isinstance(room, dict) or "name" not in room:
                return False

        return True

    def _validate_room_response(self, response: Dict) -> bool:
        if not isinstance(response, dict):
            return False

        if "description" not in response:
            return False

        return True

    def build_room(self, room_data: Dict) -> bool:
        """Build a single room based on room data."""

        room_id = room_data["id"]
        room_name = room_data["name"]
        room_description = room_data.get("description", "")
        logger.info(f"Building room: {room_name}")

        # Create the room
        place = Place(None, self.world, {"name": room_name}, recursive=False)
        place.description = room_description
        place.coordinates = room_data.get("coordinates", [0, 0, 0])
        place._save()
        print(f"Made: {place}")

        self.built_rooms[room_id] = place
        return True

    def connect_rooms(self, from_id, exit_data) -> bool:
        """Create bidirectional exits between two rooms."""

        from_room = self.built_rooms[from_id]
        to_id = exit_data["to_room"]
        if to_id == 0:
            to_room = self.player.location
        elif to_id in self.built_rooms:
            to_room = self.built_rooms[to_id]
        else:
            logger.error(f"Invalid room ID: {to_id}")
            return False

        # Make the exits
        ex_data = {"name": exit_data["dir"], "source": None, "destination": None}
        if "class" in exit_data and exit_data["class"] == "OpenableExit":
            exit = OpenableExit(None, self.world, ex_data, recursive=False)
        else:
            exit = Exit(None, self.world, ex_data, recursive=False)
        exit.source = from_room
        exit.destination = to_room
        from_room.add_exit(exit)
        exit._save()

        # Create the reverse exit
        rev_ex_data = {"name": exit_data["return"], "source": None, "destination": None}
        rev_exit = Exit(None, self.world, rev_ex_data, recursive=False)
        rev_exit.source = to_room
        rev_exit.destination = from_room
        to_room.add_exit(rev_exit)
        rev_exit._save()
        from_room._save()
        to_room._save()

        return True

    def setup_room(self, room, data):
        room.description = data["description"]
        room._save()
        if "objects" in data:
            for obj_data in data["objects"]:
                oname = obj_data["name"]
                oclass = obj_data["class"]
                onotes = obj_data.get("description", "")
                ocls = self.object_class_hash.get(oclass, None)
                if ocls is not None:
                    odata = {"name": oname, "description": onotes}
                    obj = ocls(None, self.world, odata, recursive=False)
                    obj.move_to(room)
                    obj._save()
                    print(f"Created {obj.name}")
                else:
                    print(f"Failed to create a {oclass} called {oname} ")
            room._save()

    def build_environment(self, description: str) -> bool:
        """Build an entire environment based on a description."""
        if self.building_in_progress:
            logger.warning("Building already in progress")
            return False

        self.building_in_progress = True

        try:
            # Generate room plan using LLM
            logger.info("Generating room plan...")
            plan = self.generate_room_plan(description, self.player.location)
            self.plan = plan

            if not plan:
                logger.error("Failed to generate room plan")
                return False

            print(plan)
            logger.info(f"Generated plan with {len(plan.get('rooms', []))} rooms")

            # Display the map
            self.print_map_grid(plan)

            # Build each room
            for room_data in plan.get("rooms", []):
                self.build_room(room_data)

            # Connect them with exits
            for room_data in plan["rooms"]:
                for ex in room_data["exits"]:
                    self.connect_rooms(room_data["id"], ex)

            # Now describe them
            for room in self.built_rooms.values():
                d = self.generate_room_description(room)
                if d is not None:
                    self.setup_room(room, d)

        except Exception as e:
            logger.error(f"build_failed: {e}")
            return False
        finally:
            self.building_in_progress = False

    def print_map_grid(self, room_data):
        """Print an ASCII grid map of the rooms based on their coordinates and exits."""
        if not room_data or "rooms" not in room_data:
            print("No room data to display")
            return

        rooms = room_data["rooms"]
        if not rooms:
            print("No rooms to display")
            return

        # Create a mapping of coordinates to room data
        coord_to_room = {}
        for room in rooms:
            coords = tuple(room["coordinates"])  # (x, y, z)
            coord_to_room[coords] = room

        # For simplicity, we'll display the Z=0 level. Could be extended for 3D
        z_level = 0

        # Find the bounds of our grid
        x_coords = [coords[0] for coords in coord_to_room.keys() if coords[2] == z_level]
        y_coords = [coords[1] for coords in coord_to_room.keys() if coords[2] == z_level]

        if not x_coords or not y_coords:
            print("No rooms at Z=0 level to display")
            return

        min_x, max_x = min(x_coords), max(x_coords)
        min_y, max_y = min(y_coords), max(y_coords)

        # Calculate grid dimensions (each room takes 3x3 cells for room + connections)
        grid_width = (max_x - min_x + 1) * 3
        grid_height = (max_y - min_y + 1) * 3

        # Initialize grid with spaces
        grid = [[" " for _ in range(grid_width)] for _ in range(grid_height)]

        # Place rooms and exits in the grid
        for room in rooms:
            x, y, z = room["coordinates"]
            if z != z_level:
                continue

            # Convert world coordinates to grid coordinates
            grid_x = (x - min_x) * 3 + 1  # Center of the 3x3 cell
            grid_y = (max_y - y) * 3 + 1  # Flip Y axis for display (top-down view)

            # Place room ID in the center
            room_id_str = str(room["id"])
            if len(room_id_str) == 1:
                grid[grid_y][grid_x] = room_id_str
            else:
                # For multi-digit IDs, try to fit them
                grid[grid_y][grid_x] = room_id_str[0]
                if grid_x + 1 < grid_width:
                    grid[grid_y][grid_x + 1] = room_id_str[1] if len(room_id_str) > 1 else " "

            # Add exits
            for exit_info in room.get("exits", []):
                direction = exit_info["dir"].lower()

                if direction == "north":
                    if grid_y > 0:
                        grid[grid_y - 1][grid_x] = "|"
                elif direction == "south":
                    if grid_y < grid_height - 1:
                        grid[grid_y + 1][grid_x] = "|"
                elif direction == "east":
                    if grid_x < grid_width - 1:
                        grid[grid_y][grid_x + 1] = "-"
                        if grid_x + 2 < grid_width:
                            grid[grid_y][grid_x + 2] = "-"
                elif direction == "west":
                    if grid_x > 0:
                        grid[grid_y][grid_x - 1] = "-"
                        if grid_x - 2 >= 0:
                            grid[grid_y][grid_x - 2] = "-"
                elif direction == "northeast":
                    if grid_y > 0 and grid_x < grid_width - 1:
                        grid[grid_y - 1][grid_x + 1] = "\\"
                elif direction == "northwest":
                    if grid_y > 0 and grid_x > 0:
                        grid[grid_y - 1][grid_x - 1] = "/"
                elif direction == "southeast":
                    if grid_y < grid_height - 1 and grid_x < grid_width - 1:
                        grid[grid_y + 1][grid_x + 1] = "/"
                elif direction == "southwest":
                    if grid_y < grid_height - 1 and grid_x > 0:
                        grid[grid_y + 1][grid_x - 1] = "\\"

        # Print the grid
        print(f"\nRoom Map (Z={z_level} level):")
        print("=" * grid_width)
        for row in grid:
            print("".join(row))
        print("=" * grid_width)

        # Print room list for reference
        print("Rooms:")
        for room in sorted(rooms, key=lambda r: r["id"]):
            x, y, z = room["coordinates"]
            print(f"  {room['id']}: {room['name']} at ({x},{y},{z})")
        print()

    def run_interactive_mode(self):
        """Run the agent in interactive mode, accepting building requests."""
        print("Room Builder Agent - Interactive Mode")
        print("Type 'quit' to exit, or describe an environment to build.")
        print()

        while True:
            try:
                description = input("> ").strip()
                if description.lower() in ["quit", "exit", "q"]:
                    break
                elif description:
                    print("Building environment... (this may take a few minutes)")
                    success = self.build_environment(description)
                    if success:
                        print("Environment built successfully!")
                    else:
                        print("Failed to build environment.")

            except KeyboardInterrupt:
                print("Exiting...")
                break
