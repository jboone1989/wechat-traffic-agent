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

The repository provides both a systemd timer and an equivalent user crontab.
VM-0-9 currently uses `deploy/cron/ferro-screeps.crontab` because Remote does
not require root access to install it. Hosts with root-managed systemd can use
`ferro-screeps-trainer.service` plus `ferro-screeps-trainer.timer`.

Both schedules attempt one 180-tick match every 10 minutes. Training is
automatically deferred when any of these conditions is true:

- available memory < 900 MiB;
- free disk < 10 GiB;
- one-minute load average > 1.5.

The cron deployment runs under `nice -n 10` and idle I/O priority, while the
systemd deployment additionally caps training at 85% of one CPU and 1 GiB of
memory. The trainer itself always uses an exclusive lock, a 240-second match
timeout, and the resource admission gates above. Successful matches remove
their temporary Loki database/log directory; compact inputs/results and Ferro
learning state remain durable.

Both deployment modes expect:

- `/home/ubuntu/ferro-screeps-current` -> deployed Ferro release;
- `/home/ubuntu/content-agent-venv-current` -> stable Python environment;
- `/home/ubuntu/screeps-lab` -> local Screeps installation;
- `/home/ubuntu/content-agent-state` -> production Ferro state.
