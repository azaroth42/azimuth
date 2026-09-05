
This file describes the requirements for agents interacting with the world, and the different roles of agents in building it.

There are the following roles:

* Storyteller. The storyteller is responsible for the plot of the game. The storyteller creates a series of descriptions of the world to be built in areas by the following agents. It gives the descriptions to the Architect to lay out the overall structure. It gives descriptions to the Director for which characters to create and what role they will play in the story, and which area they will be placed in.
* Architect.  The architect agent takes a description of an entire area from the Storyteller and designs the layout in terms of the Places and their connnections via exits. The architect names the rooms and leaves short descriptions of the intent. The architect needs to think big and plan ahead. It should first design the overall layout in a grid, and then detail out each section. 
* Designer.  The designer agent takes the output of the architect and adds more detail to the room descriptions. It leaves notes for the Builder to then come and add objects to the room. The designer needs to think about the room, be very creative in terms of the descriptions, and leave clear notes for the builder to follow.
* Builder.  The builder agent takes the output of the designer and adds objects to the room. The builder needs a good understanding of the classes of object and mixin such that it can accurately create the right environment. The builder then leaves notes back to the Designer to come back and describe the objects.
* Director. The director sets up non-player characters, their initial positions and descriptions, and the notes about their personality. It sets up what each NPC knows and how they interact with the players. It sets up their statistics in the game.
* Programmer. The Programmer agent takes the output from all other agents and determines whether or not the game code needs additional functionality. If it does, the Programmer first determines whether the code is server level (e.g. it requires the game to be restarted) or whether it is database level (e.g. it is stored on a specific object in the database, rather than a general class). If it is server level, the Programmer writes detailed notes about the code changes required and gives it to Wizard. If it is at the database level, then it writes the functions on the appropriate objects.
* Wizard. The Wizard agent runs outside of the game. It implements core code changes to the server as required by the Programmer. It proposes the changes to the (human) server administrator to approve.

The different agents work together to ensure that the game code is up-to-date and meets the needs of the players.
All agents run using the Qwen3.8 27B model locally.
