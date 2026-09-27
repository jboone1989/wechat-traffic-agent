#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function numericStrategy(raw) {
  const n = (key, fallback) => {
    const value = Number(raw[key]);
    return Number.isFinite(value) ? value : fallback;
  };
  return {
    worker_target: Math.max(1, Math.min(8, Math.trunc(n("worker_target", 4)))),
    builder_target: Math.max(0, Math.min(4, Math.trunc(n("builder_target", 0)))),
    upgrader_target: Math.max(0, Math.min(4, Math.trunc(n("upgrader_target", 0)))),
    soldier_target: Math.max(0, Math.min(6, Math.trunc(n("soldier_target", 1)))),
    aggression: Math.max(0, Math.min(1, n("aggression", 0.35))),
    reserve_energy: Math.max(0, Math.min(0.8, n("reserve_energy", 0.25))),
  };
}

function botModules(strategy, homeRoom, enemyRoom) {
  const cfg = JSON.stringify({
    ...numericStrategy(strategy),
    home_room: String(homeRoom),
    enemy_room: String(enemyRoom),
    seeded_army: true,
  });
  const lines = [
    '"use strict";',
    "const CFG = " + cfg + ";",
    "function countRole(role) {",
    "  let n = 0;",
    "  for (const name in Game.creeps) if ((Game.creeps[name].memory.role || 'worker') === role) n += 1;",
    "  return n;",
    "}",
    "function spawnRole(spawn, role) {",
    "  if (!spawn || spawn.spawning) return false;",
    "  const body = role === 'soldier' ? [TOUGH, ATTACK, MOVE, MOVE] : [WORK, CARRY, MOVE];",
    "  const name = role + '-' + Game.time + '-' + Math.floor(Math.random() * 10000);",
    "  const result = spawn.spawnCreep(body, name, {memory:{role:role}});",
    "  if (result === OK) {",
    "    Memory.stats = Memory.stats || {};",
    "    Memory.stats.spawned = (Memory.stats.spawned || 0) + 1;",
    "    Memory.stats.spawnedByRole = Memory.stats.spawnedByRole || {};",
    "    Memory.stats.spawnedByRole[role] = (Memory.stats.spawnedByRole[role] || 0) + 1;",
    "    return true;",
    "  }",
    "  return false;",
    "}",
    "function runWorker(creep, spawn) {",
    "  const danger = creep.pos.findClosestByRange(FIND_HOSTILE_CREEPS, {filter:c => c.getActiveBodyparts(ATTACK) > 0 || c.getActiveBodyparts(RANGED_ATTACK) > 0});",
    "  if (danger && creep.pos.getRangeTo(danger) <= 3) {",
    "    const dx = Math.sign(creep.pos.x - danger.pos.x);",
    "    const dy = Math.sign(creep.pos.y - danger.pos.y);",
    "    creep.moveTo(Math.max(1, Math.min(48, creep.pos.x + dx)), Math.max(1, Math.min(48, creep.pos.y + dy)), {reusePath:2});",
    "    return;",
    "  }",
    "  if (creep.store.getFreeCapacity(RESOURCE_ENERGY) > 0) {",
    "    const source = creep.pos.findClosestByPath(FIND_SOURCES_ACTIVE);",
    "    if (source && creep.harvest(source) === ERR_NOT_IN_RANGE) creep.moveTo(source, {reusePath:5});",
    "    return;",
    "  }",
    "  if (spawn && creep.transfer(spawn, RESOURCE_ENERGY) === ERR_NOT_IN_RANGE) creep.moveTo(spawn, {reusePath:5});",
    "}",
    "function runSoldier(creep, spawn) {",
    "  const hostile = creep.pos.findClosestByPath(FIND_HOSTILE_CREEPS);",
    "  const hostileSpawn = creep.pos.findClosestByPath(FIND_HOSTILE_SPAWNS);",
    "  const target = hostile || (CFG.aggression >= 0.35 ? hostileSpawn : null);",
    "  if (target) {",
    "    if (creep.attack(target) === ERR_NOT_IN_RANGE && (CFG.aggression >= 0.25 || !spawn || creep.pos.getRangeTo(spawn) <= 9)) creep.moveTo(target, {reusePath:2});",
    "    return;",
    "  }",
    "  if (CFG.aggression >= 0.35 && CFG.enemy_room && creep.room.name !== CFG.enemy_room) {",
    "    creep.moveTo(new RoomPosition(25, 25, CFG.enemy_room), {reusePath:5});",
    "    return;",
    "  }",
    "  if (spawn && creep.pos.getRangeTo(spawn) > 5) creep.moveTo(spawn, {reusePath:3});",
    "}",
    "module.exports.loop = function() {",
    "  Memory.stats = Memory.stats || {spawned:0, spawnedByRole:{}};",
    "  const spawn = Object.values(Game.spawns)[0];",
    "  if (!spawn) { Memory.stats.spawnAlive = false; Memory.stats.alive = Object.keys(Game.creeps).length; return; }",
    "  const economyTarget = CFG.worker_target + CFG.builder_target + CFG.upgrader_target;",
    "  const workers = countRole('worker');",
    "  const soldiers = countRole('soldier');",
    "  const energy = spawn.store[RESOURCE_ENERGY] || 0;",
    "  const capacity = spawn.store.getCapacity(RESOURCE_ENERGY) || 300;",
    "  const reserve = Math.floor(capacity * CFG.reserve_energy);",
    "  if (!CFG.seeded_army && !spawn.spawning) {",
    "    if (workers < economyTarget && energy >= 200) spawnRole(spawn, 'worker');",
    "    else if (soldiers < CFG.soldier_target && energy >= 190 && energy - 190 >= reserve) spawnRole(spawn, 'soldier');",
    "    else if (workers < Math.max(2, economyTarget) && energy >= 200) spawnRole(spawn, 'worker');",
    "  }",
    "  for (const name in Game.creeps) {",
    "    const creep = Game.creeps[name];",
    "    if ((creep.memory.role || 'worker') === 'soldier') runSoldier(creep, spawn); else runWorker(creep, spawn);",
    "  }",
    "  Memory.stats.spawnAlive = true;",
    "  Memory.stats.alive = Object.keys(Game.creeps).length;",
    "  Memory.stats.energy = energy;",
    "  Memory.stats.tick = Game.time;",
    "};",
  ];
  return { main: lines.join("\n") };
}

async function userMemory(user) {
  try {
    const raw = await user.memory;
    return typeof raw === "string" ? JSON.parse(raw || "{}") : (raw || {});
  } catch (_) {
    return {};
  }
}

function energyIn(objects, userId) {
  let total = 0;
  for (const obj of objects) {
    if (String(obj.user || "") !== String(userId)) continue;
    if (obj.store && Number.isFinite(Number(obj.store.energy))) total += Number(obj.store.energy);
  }
  return total;
}

function owned(objects, userId, type) {
  return objects.filter(o => o.type === type && String(o.user || "") === String(userId));
}

function initialCounts(strategy) {
  const cfg = numericStrategy(strategy);
  return {
    workers: Math.max(1, Math.min(8, cfg.worker_target + cfg.builder_target + cfg.upgrader_target)),
    soldiers: Math.max(0, Math.min(5, cfg.soldier_target)),
  };
}

function creepBody(role) {
  return role === "soldier"
    ? ["tough", "attack", "move", "move"]
    : ["work", "carry", "move"];
}

async function seedArmy(server, user, room, strategy, originX, originY) {
  const terrain = await server.world.getTerrain(room);
  const current = await server.world.roomObjects(room);
  const occupied = new Set(current.map(o => String(o.x) + ":" + String(o.y)));
  const slots = [];
  for (let radius = 1; radius <= 8 && slots.length < 20; radius += 1) {
    for (let dx = -radius; dx <= radius && slots.length < 20; dx += 1) {
      for (let dy = -radius; dy <= radius && slots.length < 20; dy += 1) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== radius) continue;
        const x = originX + dx;
        const y = originY + dy;
        if (x < 1 || x > 48 || y < 1 || y > 48) continue;
        if (terrain.get(x, y) === "wall") continue;
        const key = String(x) + ":" + String(y);
        if (occupied.has(key)) continue;
        occupied.add(key);
        slots.push([x, y]);
      }
    }
  }
  const counts = initialCounts(strategy);
  const roles = [
    ...Array(counts.workers).fill("worker"),
    ...Array(counts.soldiers).fill("soldier"),
  ];
  if (slots.length < roles.length) throw new Error("not enough open tiles for seeded army");
  const memory = {
    creeps: {},
    stats: {
      spawned: roles.length,
      spawnedByRole: {worker: counts.workers, soldier: counts.soldiers},
      seededArmy: true,
    },
  };
  for (let index = 0; index < roles.length; index += 1) {
    const role = roles[index];
    const name = role + "-seed-" + index;
    const body = creepBody(role);
    const [x, y] = slots[index];
    memory.creeps[name] = {role};
    await server.world.addRoomObject(room, "creep", x, y, {
      name,
      user: user.id,
      body: body.map(type => ({type, hits: 100})),
      store: {energy: 0},
      storeCapacity: role === "worker" ? 50 : 0,
      hits: body.length * 100,
      hitsMax: body.length * 100,
      spawning: false,
      fatigue: 0,
      notifyWhenAttacked: true,
    });
  }
  const { env } = await server.world.load();
  await env.set(env.keys.MEMORY + user.id, JSON.stringify(memory));
  return counts;
}

async function main() {
  const inputPath = process.argv[2];
  if (!inputPath) throw new Error("usage: screeps_match.js INPUT.json");
  const input = readJson(inputPath);
  const labRoot = path.resolve(input.lab_root || process.cwd());
  const { ScreepsServer } = require(
    path.join(labRoot, "node_modules", "@screepts", "screeps-test-server")
  );
  const matchId = String(input.episode_id || ("smoke-" + Date.now())).replace(/[^A-Za-z0-9_.:-]/g, "_");
  const serverPath = path.join(labRoot, "matches", matchId);
  const logdir = path.join(serverPath, "logs");
  fs.mkdirSync(logdir, { recursive: true });

  const port = Number(input.port || 22125);
  const ticks = Math.max(20, Math.min(1500, Math.trunc(Number(input.ticks || 300))));
  const ferroStrategy = numericStrategy(input.strategy || {});
  const opponentStrategy = numericStrategy(input.opponent_strategy || {});
  const server = new ScreepsServer({ path: serverPath, logdir, port });
  server.on("error", message => process.stderr.write("[screeps] " + String(message) + "\n"));

  try {
    await server.world.reset();
    await server.world.stubWorld();
    const ferroRoom = String(input.ferro_room || "W0N1");
    const opponentRoom = String(input.opponent_room || "W1N1");
    const ferro = await server.world.addBot({
      username: "Ferro", room: ferroRoom, x: 15, y: 15, spawnName: "FerroSpawn", cpu: 60,
      modules: botModules(ferroStrategy, ferroRoom, opponentRoom),
    });
    const opponent = await server.world.addBot({
      username: "Opponent", room: opponentRoom, x: 35, y: 35, spawnName: "OpponentSpawn", cpu: 60,
      modules: botModules(opponentStrategy, opponentRoom, ferroRoom),
    });

    // Accelerated training starts with an equal large spawn-energy reserve.
    // This skips hundreds of low-information bootstrap ticks while all
    // movement, harvesting, spawning and combat still use the real engine.
    const { db } = await server.world.load();
    for (const [roomName, user] of [[ferroRoom, ferro], [opponentRoom, opponent]]) {
      await db["rooms.objects"].update(
        { room: roomName, type: "spawn", user: user.id },
        {
          $set: {
            store: { energy: 3000 },
            storeCapacityResource: { energy: 3000 },
          },
        }
      );
      await db["rooms.objects"].update(
        { room: roomName, type: "controller" }, { $set: { safeMode: null } });
    }
    await seedArmy(server, ferro, ferroRoom, ferroStrategy, 15, 15);
    await seedArmy(server, opponent, opponentRoom, opponentStrategy, 35, 35);

    await server.start();
    let lastObjects = [
      ...(await server.world.roomObjects(ferroRoom)),
      ...(await server.world.roomObjects(opponentRoom)),
    ];
    for (let i = 0; i < ticks; i += 1) {
      await server.tick();
      if ((i + 1) % 25 === 0 || i + 1 === ticks) {
        lastObjects = [
          ...(await server.world.roomObjects(ferroRoom)),
          ...(await server.world.roomObjects(opponentRoom)),
        ];
        if (owned(lastObjects, ferro.id, "spawn").length === 0 || owned(lastObjects, opponent.id, "spawn").length === 0) break;
      }
    }

    lastObjects = [
      ...(await server.world.roomObjects(ferroRoom)),
      ...(await server.world.roomObjects(opponentRoom)),
    ];
    const ferroMemory = await userMemory(ferro);
    const opponentMemory = await userMemory(opponent);
    const ferroCreeps = owned(lastObjects, ferro.id, "creep");
    const opponentCreeps = owned(lastObjects, opponent.id, "creep");
    const ferroSpawns = owned(lastObjects, ferro.id, "spawn");
    const opponentSpawns = owned(lastObjects, opponent.id, "spawn");
    const ferroSpawned = Number((ferroMemory.stats || {}).spawned || 0);
    const opponentSpawned = Number((opponentMemory.stats || {}).spawned || 0);
    const ferroLosses = Math.max(0, ferroSpawned - ferroCreeps.length);
    const opponentLosses = Math.max(0, opponentSpawned - opponentCreeps.length);
    const ferroEnergy = energyIn(lastObjects, ferro.id);
    const opponentEnergy = energyIn(lastObjects, opponent.id);
    const ferroAlive = ferroSpawns.length > 0;
    const opponentAlive = opponentSpawns.length > 0;
    const ferroRooms = owned(lastObjects, ferro.id, "controller").length;
    const opponentRooms = owned(lastObjects, opponent.id, "controller").length;

    let winner = "DRAW";
    if (ferroAlive && !opponentAlive) winner = "FERRO";
    else if (!ferroAlive && opponentAlive) winner = "OPPONENT";
    else if (ferroEnergy + ferroCreeps.length * 50 > opponentEnergy + opponentCreeps.length * 50 + 50) winner = "FERRO";
    else if (opponentEnergy + opponentCreeps.length * 50 > ferroEnergy + ferroCreeps.length * 50 + 50) winner = "OPPONENT";

    process.stdout.write(JSON.stringify({
      schema_version: "ferro-screeps-episode.v2",
      episode_id: input.episode_id,
      evidence_ref: "screeps-match:" + matchId,
      observed_at: new Date().toISOString(),
      winner,
      ticks_requested: ticks,
      game_time: await server.world.gameTime,
      metrics: {
        survived: ferroAlive,
        match_outcome: winner === "FERRO" ? 1 : (winner === "OPPONENT" ? -1 : 0),
        owned_rooms_start: 1,
        owned_rooms_end: ferroRooms,
        energy_start: 300,
        energy_end: ferroEnergy,
        controller_progress_start: 0,
        controller_progress_end: 0,
        hostile_creeps_destroyed: opponentLosses,
        own_creeps_lost: ferroLosses,
        structures_start: 1,
        structures_end: ferroSpawns.length,
      },
      opponent_metrics: {
        survived: opponentAlive,
        owned_rooms_start: 1,
        owned_rooms_end: opponentRooms,
        energy_start: 300,
        energy_end: opponentEnergy,
        hostile_creeps_destroyed: ferroLosses,
        own_creeps_lost: opponentLosses,
        structures_start: 1,
        structures_end: opponentSpawns.length,
      },
      telemetry: {
        ferro: {user_id: ferro.id, alive_creeps: ferroCreeps.length, spawned_creeps: ferroSpawned, memory: ferroMemory.stats || {}},
        opponent: {user_id: opponent.id, alive_creeps: opponentCreeps.length, spawned_creeps: opponentSpawned, memory: opponentMemory.stats || {}},
      },
    }) + "\n");
  } finally {
    try { server.stop(); } catch (_) {}
  }
}

main()
  .then(() => setTimeout(() => process.exit(0), 100))
  .catch(error => {
    process.stderr.write((error && error.stack) ? error.stack + "\n" : String(error) + "\n");
    setTimeout(() => process.exit(1), 100);
  });
