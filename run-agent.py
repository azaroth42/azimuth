#!/usr/bin/env python3
"""
Room Builder Agent Examples

This script demonstrates various usage patterns for the Room Builder Agent,
showing how to build different types of environments programmatically.
"""

import asyncio
import logging
import sys
import os

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from azimuth.agents.room_builder import RoomBuilderAgent
from azimuth.agents.config import AgentConfig

# Configure logging for examples
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def example_basic_usage():
    """Basic example: Connect and build a simple environment."""
    print("🏗️  Starting Room Builder Agent Example")

    try:
        # Create agent with default configuration
        print("Creating agent with configuration...")
        agent = RoomBuilderAgent()

        # Print configuration info
        config = agent.config
        print(f"MUD Server: {config.mud_server_url}")
        print(f"Username: {config.mud_username}")
        print(f"LM Studio: {config.lm_studio_url}")

        # Connect to MUD
        print("\nConnecting to MUD server...")
        if not await agent.connect_to_mud():
            print("❌ Failed to connect to MUD server")
            print("Make sure the Azimuth server is running: python run.py")
            return

        print("✅ Connected to MUD server")

        # Login
        print("Logging in...")
        if not await agent.login():
            print("❌ Failed to login")
            print(f"Check username ({config.mud_username}) and password are correct")
            return

        print("✅ Successfully logged in")

        # Test basic command
        print("Testing basic command...")
        response = await agent.send_command("look")
        if response:
            print(f"Command response: {response[:100]}...")
        else:
            print("⚠️  No response to test command")

        # Build a simple environment
        description = "a dark swamp with a small cabin"
        print(f"\nBuilding: {description}")

        success = await agent.build_environment(description)
        if success:
            print("✅ Environment built successfully!")
        else:
            print("❌ Failed to build environment")

    except Exception as e:
        print(f"❌ Error during example: {e}")
        logger.error(f"Example failed with error: {e}", exc_info=True)
    finally:
        # Cleanup
        try:
            if "agent" in locals() and agent.sio.connected:
                print("Disconnecting...")
                await agent.sio.disconnect()
                print("✅ Disconnected")
        except Exception as e:
            print(f"Error during cleanup: {e}")


async def test_connections():
    """Test connections to both MUD and LM Studio before building."""
    print("🔍 Testing Connections...")

    # Test MUD connection
    try:
        config = AgentConfig()
        agent = RoomBuilderAgent(config=config)

        print(f"Testing MUD connection to {config.mud_server_url}...")
        mud_connected = await agent.connect_to_mud()

        if mud_connected:
            print("✅ MUD connection successful")

            print("Testing login...")
            login_success = await agent.login()

            if login_success:
                print("✅ Login successful")
                await agent.sio.disconnect()
                return True
            else:
                print("❌ Login failed")
                await agent.sio.disconnect()
                return False
        else:
            print("❌ MUD connection failed")
            return False

    except Exception as e:
        print(f"❌ Connection test failed: {e}")
        return False


async def main():
    """Main example runner with interactive menu."""
    print("🤖 Azimuth Room Builder Agent - Examples")
    print("=" * 50)

    # Test connections first
    if not await test_connections():
        print("\n❌ Connection tests failed. Please check:")
        print("1. MUD server is running (python run.py)")
        print("2. Username/password are correct")
        print("3. Server is accessible at the configured URL")
        return

    print("\n🎉 All connections working! Running example...")
    print("=" * 50)

    await example_basic_usage()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Exiting...")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        logger.error(f"Fatal error in main: {e}", exc_info=True)
