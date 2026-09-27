# Ferro Screeps training

Ferro uses a local Screeps engine as an external competitive world. The trainer
runs one bounded PvP episode at a time and settles learning only from measured
game outcomes.

## Learning invariants

- Process/function success is not reward.
- Match outcome, survival, territory, energy, controller progress, combat
  exchange and infrastructure are measured from the game world.
- Harness failures cancel the world-learning commitment without positive or
  negative policy reward.
- Strategy challengers require prospective baseline/challenger matches before
  promotion.
- Screeps uses its own `world_learning.screeps.json` namespace.
- The v2 episode schema includes competitive match outcome; v1 results are
  retained as historical files but are not loaded into v2 research state.

## Continuous training

Production uses `ferro-screeps-trainer.service` plus
`ferro-screeps-trainer.timer`. The timer attempts one 180-tick match every
10 minutes. Training is automatically deferred when any of these conditions is
true:

- available memory < 900 MiB;
- free disk < 10 GiB;
- one-minute load average > 1.5.

The systemd cgroup additionally limits training to 85% of one CPU and 1 GiB of
memory. Successful matches remove their temporary Loki database/log directory;
compact inputs/results and Ferro learning state remain durable.

The service expects:

- `/home/ubuntu/ferro-screeps-current` -> deployed Ferro release;
- `/home/ubuntu/screeps-lab` -> local Screeps installation;
- `/home/ubuntu/content-agent-state` -> production Ferro state.
