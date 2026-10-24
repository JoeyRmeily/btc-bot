# Working style: use background agents

The user wants fast results. You don't need to wait for the user to ask before spawning subagents: they have given standing permission to use them whenever doing so speeds up a task.

## When to spawn
- The task splits into independent parts, for example backtesting several coins or windows, researching several ideas, or searching different parts of the codebase. Launch one agent per part **in a single message** so they run in parallel.
- Long-running work (backtests, data downloads, server log digging) can run in the background while you keep working on the rest.
- Broad codebase searches: use the `Explore` agent.
- Use as many agents as the work actually splits into. There is no fixed cap.

## When not to spawn
- Small or sequential tasks, such as a one-line fix, a single file read or a quick question. Do these directly, because an agent starts cold and would be slower.
- Steps that depend on each other's results. Run them in order instead.

## Rules for agents
- Give each agent a self-contained prompt with the file paths, the goal and the expected output format.
- Agents must never push to `main`, restart the server or touch `.env`, because every push is live (see deployment.md). Only the main session deploys, and only after the user approves.
- Combine the agents' results into one short answer for the user.
