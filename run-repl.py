import os
from azimuth.persistence import SimpleFileStorage, MlStorage
from azimuth.world import setup_world
import dotenv

from azimuth.agents.config import AgentConfig
from azimuth.agents.room_builder import RoomBuilderAgent

dotenv.load_dotenv()
world_id = os.getenv("AZIMUTH_WORLD_ID", "WORLD1")
db_type = os.getenv("AZIMUTH_DB_TYPE", "file")

if db_type == "file":
    db = SimpleFileStorage()
elif db_type == "marklogic":
    url = os.getenv("AZIMUTH_ML_URL", "http://localhost:8000")
    user = os.getenv("AZIMUTH_ML_USER", "admin")
    password = os.getenv("AZIMUTH_ML_PASSWORD")
    dbname = os.getenv("AZIMUTH_ML_DB", "azimuth")
    db = MlStorage(url, user, password, dbname)

world = setup_world(db, world_id)
if not world:
    print("FATAL: Could not initialize world.")
    exit(1)


def go(exit_name):
    if exit_name in player.location.exits:
        player.location.exits[exit_name].use(player)
    else:
        print(f"Invalid exit: {exit_name}")


def connect(world):
    global player
    player = world.load(world.players["wizard"])
    start = world.config["start_room_id"]
    sloc = world.load(start)
    player.move_to(sloc)
    return player


def make_agent(world):
    agent_config = AgentConfig()
    agent = RoomBuilderAgent(world, agent_config)
    return agent


# Run an architect prompt to build the skeleton of the rooms
# The architect will create a grid layout with names based on a theme given to the function, and the current room name and description
