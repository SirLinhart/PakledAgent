#!/usr/bin/env node
/**
 * One Pokémon Showdown battle, spoken over JSON lines: practice tooling only.
 *
 * The Python practice harness (ucla_agent.practice.pokemon.bridge) starts this
 * script, writes one command per line on stdin and reads exactly one answer
 * line on stdout for each command:
 *
 *   {"command": "start", "format": "gen9vgc2025regi", "seed": "1,2,3,4",
 *    "players": {"p1": {"name": "...", "team": [<set>, ...]}, "p2": {...}}}
 *   {"command": "choose", "side": "p1", "choice": "move 1 2, switch 3"}
 *
 * An answer is {"ok": true, "sides": {"p1": <side>, "p2": <side>}, "ended": bool,
 * "winner": <player name or null>}, where each <side> holds what that player
 * receives from the simulator since the previous answer: "log" (the battle log
 * lines it may see), "request" (its latest |request| object, or null) and
 * "error" (the text of an |error| line, or null). A command that cannot be run
 * answers {"ok": false, "error": "..."}.
 *
 * Showdown sends a line that differs between players as `|split|<side>`
 * followed by the owner's version (exact HP) and everyone else's (HP out of
 * 100); each side's log keeps the version that side would see.
 *
 * The battle runs inside Showdown's BattleStream, whose output arrives
 * asynchronously but without any I/O: one turn of the event loop after a
 * write, everything that write produced has been delivered.
 */
'use strict';

const readline = require('readline');
const { BattleStream } = require('pokemon-showdown');

const SIDES = ['p1', 'p2'];
const SPLIT_PREFIX = '|split|';
const REQUEST_PREFIX = '|request|';
const ERROR_PREFIX = '|error|';
const WIN_PREFIX = '|win|';
const TIE_LINE = '|tie';

class Battle {
	constructor() {
		this.stream = null;
		this.chunks = [];
		this.ended = false;
		this.winner = null;
	}

	async start(command) {
		if (this.stream) throw new Error('the battle has already started');
		for (const side of SIDES) {
			if (!command.players || !command.players[side]) throw new Error(`start needs players.${side}`);
		}
		// noCatch: a Showdown exception reaches this script as an exception
		// instead of ending the stream, so it is answered as {"ok": false}.
		this.stream = new BattleStream({ noCatch: true });
		this.collect();
		this.stream.write(`>start ${JSON.stringify({ formatid: command.format, seed: command.seed })}`);
		for (const side of SIDES) {
			const player = command.players[side];
			this.stream.write(`>player ${side} ${JSON.stringify({ name: player.name, team: player.team })}`);
		}
		return this.answer();
	}

	async choose(command) {
		if (!this.stream) throw new Error('the battle has not started');
		if (this.ended) throw new Error('the battle is over');
		if (!SIDES.includes(command.side)) throw new Error(`unknown side ${JSON.stringify(command.side)}`);
		if (typeof command.choice !== 'string') throw new Error('choose needs a choice string');
		this.stream.write(`>${command.side} ${command.choice}`);
		return this.answer();
	}

	collect() {
		(async () => {
			for await (const chunk of this.stream) this.chunks.push(chunk);
		})();
	}

	async answer() {
		await new Promise(resolve => setImmediate(resolve));
		const sides = {};
		for (const side of SIDES) sides[side] = { log: [], request: null, error: null };
		for (const chunk of this.chunks.splice(0)) this.read(chunk, sides);
		return { ok: true, sides, ended: this.ended, winner: this.winner };
	}

	/** One output chunk of the stream: `update` (the shared log), `sideupdate` (one side's lines) or `end`. */
	read(chunk, sides) {
		const [type, ...rest] = chunk.split('\n');
		if (type === 'update') {
			this.readUpdate(rest, sides);
		} else if (type === 'sideupdate') {
			const [side, ...lines] = rest;
			for (const line of lines) readSideLine(line, sides[side]);
		} else if (type === 'end') {
			this.ended = true;
			this.winner = JSON.parse(rest.join('\n')).winner || null;
		}
	}

	readUpdate(lines, sides) {
		for (const side of SIDES) sides[side].log.push(...visibleLines(lines, side));
		for (const line of lines) this.noteEnding(line);
	}

	noteEnding(line) {
		if (line.startsWith(WIN_PREFIX)) {
			this.ended = true;
			this.winner = line.slice(WIN_PREFIX.length);
		} else if (line === TIE_LINE) {
			this.ended = true;
		}
	}
}

/** One line sent to one side only: its request, its error, or anything else for its log. */
function readSideLine(line, seen) {
	if (line.startsWith(REQUEST_PREFIX)) {
		seen.request = JSON.parse(line.slice(REQUEST_PREFIX.length));
	} else if (line.startsWith(ERROR_PREFIX)) {
		seen.error = line.slice(ERROR_PREFIX.length);
	} else {
		seen.log.push(line);
	}
}

/** The lines of one update that `side` sees: of each split pair, its own version when it owns the line. */
function visibleLines(lines, side) {
	const visible = [];
	for (let index = 0; index < lines.length; index++) {
		const line = lines[index];
		if (line.startsWith(SPLIT_PREFIX)) {
			const owner = line.slice(SPLIT_PREFIX.length);
			visible.push(owner === side ? lines[index + 1] : lines[index + 2]);
			index += 2;
		} else {
			visible.push(line);
		}
	}
	return visible.filter(line => line !== '');
}

async function main() {
	const battle = new Battle();
	const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
	for await (const line of input) {
		if (!line.trim()) continue;
		let answer;
		try {
			const command = JSON.parse(line);
			if (command.command === 'start') {
				answer = await battle.start(command);
			} else if (command.command === 'choose') {
				answer = await battle.choose(command);
			} else {
				throw new Error(`unknown command ${JSON.stringify(command.command)}`);
			}
		} catch (err) {
			answer = { ok: false, error: String(err && err.stack || err) };
		}
		process.stdout.write(`${JSON.stringify(answer)}\n`);
	}
}

main();
