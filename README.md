# AZIMUTH

Azaroth's Intelligent Multi-User Textual Habitat

## Background

A LambdaMOO-inspired python M* server, initially as an experiment in vibe coding / AI assisted development, and to update my skills with the latest python3 features.

## Running azimuth

Right now, just do:  python ./azimuth/main.py to start the flask server.
Then python ./client.py to run the text based client.

You can login as wizard, password wizard, to get a programmer.
Or just register a player and edit the JSON representation to make it a Programmer rather than a Player.

## Ongoing Work

* Finish up the base classes, mixins and commands
* Implement AI-tool usage with MCP
* Implement an AI agent framework for non-human builders and players
* More robust persistence, with redis, marklogic, postgres or other real databases
* Use uvicorn or other non-sucky server framework

## Contributing

Contributions are welcome but not expected!
