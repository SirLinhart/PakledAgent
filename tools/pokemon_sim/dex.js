#!/usr/bin/env node
/**
 * Showdown's dex facts and set validation for one JSON query: practice tooling only.
 *
 * scripts/build_pokemon_cards.py writes one JSON object on stdin:
 *
 *   {"format": "gen9vgc2025regi", "species": [...], "moves": [...], "items": [...],
 *    "abilities": [...], "natures": [...], "sets": [<set>, ...]}
 *
 * and reads one JSON object on stdout with, for each name or id asked, what
 * Showdown's dex knows of it (`exists` false when nothing), and for each set
 * the problems Showdown's validator finds for that format (null when none).
 */
'use strict';

const { Dex, TeamValidator } = require('pokemon-showdown');

function species(name) {
	const found = Dex.species.get(name);
	if (!found.exists) return { exists: false };
	const base = Dex.species.get(found.baseSpecies);
	return {
		exists: true,
		name: found.name,
		id: found.id,
		num: found.num,
		base_species: found.baseSpecies,
		types: found.types,
		base_stats: found.baseStats,
		formes: (base.formeOrder || [base.name]).filter(forme => Dex.species.get(forme).exists),
	};
}

function move(name) {
	const found = Dex.moves.get(name);
	if (!found.exists) return { exists: false };
	return {
		exists: true,
		name: found.name,
		id: found.id,
		type: found.type,
		category: found.category,
		base_power: found.basePower,
		accuracy: found.accuracy,
		priority: found.priority,
		target: found.target,
	};
}

function named(table) {
	return name => {
		const found = table.get(name);
		return found.exists ? { exists: true, name: found.name, id: found.id } : { exists: false };
	};
}

function lookUp(names, describe) {
	const answer = {};
	for (const name of names || []) answer[name] = describe(name);
	return answer;
}

function main(query) {
	const validator = TeamValidator.get(query.format);
	return {
		species: lookUp(query.species, species),
		moves: lookUp(query.moves, move),
		items: lookUp(query.items, named(Dex.items)),
		abilities: lookUp(query.abilities, named(Dex.abilities)),
		natures: lookUp(query.natures, named(Dex.natures)),
		problems: (query.sets || []).map(set => validator.validateSet(set, {})),
	};
}

let text = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', piece => { text += piece; });
process.stdin.on('end', () => {
	process.stdout.write(`${JSON.stringify(main(JSON.parse(text)))}\n`);
});
