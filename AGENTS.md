Task:

A notifier program for cursor-agent status. It should send a discord 
notification to a webhook when it notices that a previously-running 
cursor-agent task has paused or completed or is awaitign user input or 
whatever.

That's the task. If you can think of a better way than below, then please by 
all means do that. Do it the best way you know how. But my sort of brutish idea 
is as follows. Use the best language for the task (I assume either bash or 
python).

Idea: every e.g. 5-10 seconds, list all tmux panes in all tmux sessions, then 
look at the last e.g 50 lines from each pane's output. Now, cursor-agent is 
ncurses based, so it might do funky stuff in this context, but bearing that in 
mind, I notice when I use cursor-agent that when it says "n tokens" above the 
entry bar it means it's active and when that goes away it means the chat either 
hasn't started yet or has been paused or is awaiting input. If it's started and 
is now awaiting input, that's when I want to be notified.

min task: just notify me. Even better would be if I could determine the pwd of 
that pane and include that in the discord message. Even better than that would 
be if I could pop a quick temp shell in that working directory and check what 
branch it's on and include that too in the discord message.

Anyway, give it a shot
