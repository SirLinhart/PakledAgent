// Writes tests/pokemon/fixtures/damage_reference.json: the reference damage of 15 cases, plus one case per other
// modifier the estimate models, computed by the Smogon damage calculator (npm package @smogon/calc), which the
// Python damage estimate is checked against.
//
//     npm install @smogon/calc@0.12.0        (in a scratch folder under tmp/, which git ignores)
//     node tests/pokemon/fixtures/damage_reference.js <that folder>/node_modules/@smogon/calc <output .json>
//
// Every case is a Gen 9 doubles battle, at level 50 unless the set says otherwise. The fixture keeps each case's
// inputs in the shape the Python tests read, the 16 damage rolls of each hit, the summed min and max, and the
// calculator's version. The calculator is never a dependency of the agent: it is run once, by hand, to write
// this file.
'use strict';

const fs = require('fs');
const path = require('path');

const [calcFolder, outputPath] = process.argv.slice(2);
if (!calcFolder || !outputPath) {
  console.error('usage: node damage_reference.js <@smogon/calc folder> <output .json>');
  process.exit(2);
}
const calc = require(path.resolve(calcFolder));
const calcVersion = require(path.join(path.resolve(calcFolder), 'package.json')).version;

const GENERATION = 9;
const LEVEL = 50;
const GAME_TYPE = 'Doubles';

function mon(species, nature, evs, extra = {}) {
  return {species, nature, evs, item: null, ability: null, status: null, boosts: {}, ...extra};
}

function field(extra = {}) {
  return {weather: null, terrain: null, reflect: false, light_screen: false, ...extra};
}

const CASES = [
  {
    name: 'STAB special hit, neutral',
    attacker: mon('Flutter Mane', 'Timid', {spa: 252, spe: 252, hp: 4}, {ability: 'Protosynthesis'}),
    move: 'Moonblast',
    defender: mon('Rillaboom', 'Adamant', {hp: 252, spd: 4}, {ability: 'Grassy Surge'}),
    field: field(),
  },
  {
    name: 'resisted hit',
    attacker: mon('Garchomp', 'Jolly', {atk: 252, spe: 252, hp: 4}, {ability: 'Rough Skin'}),
    move: 'Dragon Claw',
    defender: mon('Gholdengo', 'Modest', {hp: 252, def: 4}, {ability: 'Good as Gold'}),
    field: field(),
  },
  {
    name: 'immune hit (Ground into Flying)',
    attacker: mon('Garchomp', 'Jolly', {atk: 252, spe: 252, hp: 4}, {ability: 'Rough Skin'}),
    move: 'Stomping Tantrum',
    defender: mon('Corviknight', 'Impish', {hp: 252, def: 252}, {ability: 'Pressure'}),
    field: field(),
  },
  {
    name: 'spread move, super effective',
    attacker: mon('Garchomp', 'Adamant', {atk: 252, spe: 252, hp: 4}, {ability: 'Rough Skin'}),
    move: 'Earthquake',
    defender: mon('Kingambit', 'Adamant', {hp: 252, def: 4}, {ability: 'Supreme Overlord'}),
    field: field(),
  },
  {
    name: 'sun-boosted Fire move',
    attacker: mon('Charizard', 'Timid', {spa: 252, spe: 252, hp: 4}, {ability: 'Blaze'}),
    move: 'Flamethrower',
    defender: mon('Amoonguss', 'Bold', {hp: 252, def: 156, spd: 100}, {ability: 'Regenerator'}),
    field: field({weather: 'Sun'}),
  },
  {
    name: 'Electric Terrain into Light Screen',
    attacker: mon('Raichu', 'Modest', {spa: 252, spe: 252, hp: 4}, {ability: 'Static'}),
    move: 'Thunderbolt',
    defender: mon('Pelipper', 'Bold', {hp: 252, def: 252, spd: 4}, {ability: 'Drizzle'}),
    field: field({terrain: 'Electric', light_screen: true}),
  },
  {
    name: 'burned physical attacker',
    attacker: mon('Kingambit', 'Adamant', {hp: 252, atk: 252, spd: 4}, {ability: 'Supreme Overlord', status: 'brn'}),
    move: 'Kowtow Cleave',
    defender: mon('Gholdengo', 'Modest', {hp: 252, def: 4}, {ability: 'Good as Gold'}),
    field: field(),
  },
  {
    name: 'Choice Specs',
    attacker: mon('Flutter Mane', 'Modest', {spa: 252, spe: 252, hp: 4}, {ability: 'Protosynthesis', item: 'Choice Specs'}),
    move: 'Shadow Ball',
    defender: mon('Gholdengo', 'Timid', {hp: 4, spa: 252, spe: 252}, {ability: 'Good as Gold'}),
    field: field(),
  },
  {
    name: 'Life Orb',
    attacker: mon('Talonflame', 'Adamant', {atk: 252, spe: 252, hp: 4}, {ability: 'Gale Wings', item: 'Life Orb'}),
    move: 'Brave Bird',
    defender: mon('Rillaboom', 'Adamant', {hp: 252, atk: 252, def: 4}, {ability: 'Grassy Surge'}),
    field: field(),
  },
  {
    name: 'Assault Vest defender',
    attacker: mon('Flutter Mane', 'Timid', {spa: 252, spe: 252, hp: 4}, {ability: 'Protosynthesis'}),
    move: 'Moonblast',
    defender: mon('Iron Hands', 'Adamant', {hp: 252, atk: 252, spd: 4}, {ability: 'Quark Drive', item: 'Assault Vest'}),
    field: field(),
  },
  {
    name: 'attack lowered one stage by Intimidate',
    attacker: mon('Urshifu-Rapid-Strike', 'Adamant', {atk: 252, spe: 252, hp: 4}, {ability: 'Unseen Fist', boosts: {atk: -1}}),
    move: 'Close Combat',
    defender: mon('Kingambit', 'Adamant', {hp: 252, def: 4}, {ability: 'Supreme Overlord'}),
    field: field(),
  },
  {
    name: 'Multiscale at full HP',
    attacker: mon('Iron Bundle', 'Timid', {spa: 252, spe: 252, hp: 4}, {ability: 'Quark Drive'}),
    move: 'Ice Beam',
    defender: mon('Dragonite', 'Adamant', {hp: 252, atk: 252, spd: 4}, {ability: 'Multiscale'}),
    field: field(),
  },
  {
    name: 'Surging Strikes: three always-crit hits through a +1 Defense, Mystic Water',
    attacker: mon('Urshifu-Rapid-Strike', 'Adamant', {atk: 252, spe: 252, hp: 4}, {ability: 'Unseen Fist', item: 'Mystic Water'}),
    move: 'Surging Strikes',
    defender: mon('Incineroar', 'Careful', {hp: 252, def: 4, spd: 252}, {ability: 'Blaze', boosts: {def: 1}}),
    field: field(),
  },
  {
    name: 'Technician',
    attacker: mon('Scizor', 'Adamant', {hp: 252, atk: 252, spd: 4}, {ability: 'Technician'}),
    move: 'Bullet Punch',
    defender: mon('Flutter Mane', 'Timid', {hp: 4, spa: 252, spe: 252}, {ability: 'Protosynthesis'}),
    field: field(),
  },
  {
    name: 'Huge Power into Reflect',
    attacker: mon('Azumarill', 'Adamant', {hp: 252, atk: 252, spd: 4}, {ability: 'Huge Power'}),
    move: 'Play Rough',
    defender: mon('Garchomp', 'Jolly', {atk: 252, spe: 252, hp: 4}, {ability: 'Rough Skin'}),
    field: field({reflect: true}),
  },
];

// One case per other modifier the estimate models, each checked the same way as the 15 cases above.
const ATTACKER = {atk: 252, spe: 252, hp: 4};
const SPECIAL_ATTACKER = {spa: 252, spe: 252, hp: 4};
const BULKY = {hp: 252, def: 252, spd: 4};
const MODIFIER_CASES = [
  ['Adaptability, STAB', mon('Porygon-Z', 'Modest', SPECIAL_ATTACKER, {ability: 'Adaptability'}), 'Hyper Beam', mon('Incineroar', 'Careful', BULKY, {ability: 'Blaze'}), field()],
  ['Adaptability, no STAB', mon('Porygon-Z', 'Modest', SPECIAL_ATTACKER, {ability: 'Adaptability'}), 'Tri Attack', mon('Incineroar', 'Careful', BULKY, {ability: 'Blaze'}), field()],
  ['Guts while burned', mon('Ursaluna', 'Adamant', ATTACKER, {ability: 'Guts', status: 'brn'}), 'Headlong Rush', mon('Incineroar', 'Careful', BULKY, {ability: 'Blaze'}), field()],
  ['Thick Fat', mon('Charizard', 'Timid', SPECIAL_ATTACKER, {ability: 'Blaze'}), 'Flamethrower', mon('Mamoswine', 'Adamant', BULKY, {ability: 'Thick Fat'}), field()],
  ['Filter, super effective', mon('Garchomp', 'Jolly', ATTACKER, {ability: 'Rough Skin'}), 'Iron Head', mon('Mr. Mime', 'Bold', BULKY, {ability: 'Filter'}), field()],
  ['Filter, neutral spread', mon('Garchomp', 'Jolly', ATTACKER, {ability: 'Rough Skin'}), 'Rock Slide', mon('Mr. Mime', 'Bold', BULKY, {ability: 'Filter'}), field()],
  ['Prism Armor, super effective', mon('Flutter Mane', 'Timid', SPECIAL_ATTACKER, {ability: 'Protosynthesis'}), 'Shadow Ball', mon('Necrozma', 'Bold', BULKY, {ability: 'Prism Armor'}), field()],
  ['Fluffy, contact', mon('Urshifu', 'Adamant', ATTACKER, {ability: 'Unseen Fist'}), 'Close Combat', mon('Dachsbun', 'Impish', BULKY, {ability: 'Fluffy'}), field()],
  ['Fluffy, Fire', mon('Charizard', 'Timid', SPECIAL_ATTACKER, {ability: 'Blaze'}), 'Flamethrower', mon('Dachsbun', 'Impish', BULKY, {ability: 'Fluffy'}), field()],
  ['Ice Scales', mon('Flutter Mane', 'Timid', SPECIAL_ATTACKER, {ability: 'Protosynthesis'}), 'Moonblast', mon('Frosmoth', 'Bold', BULKY, {ability: 'Ice Scales'}), field()],
  ['Expert Belt, spread', mon('Garchomp', 'Jolly', ATTACKER, {ability: 'Rough Skin', item: 'Expert Belt'}), 'Earthquake', mon('Kingambit', 'Adamant', BULKY, {ability: 'Supreme Overlord'}), field()],
  ['Eviolite', mon('Garchomp', 'Jolly', ATTACKER, {ability: 'Rough Skin'}), 'Dragon Claw', mon('Dusclops', 'Relaxed', BULKY, {ability: 'Frisk', item: 'Eviolite'}), field()],
  ['Tough Claws', mon('Meowscarada', 'Jolly', ATTACKER, {ability: 'Tough Claws'}), 'Flower Trick', mon('Urshifu-Rapid-Strike', 'Adamant', BULKY, {ability: 'Unseen Fist'}), field()],
  ['Sheer Force, secondary effect', mon('Landorus', 'Timid', SPECIAL_ATTACKER, {ability: 'Sheer Force'}), 'Earth Power', mon('Kingambit', 'Adamant', BULKY, {ability: 'Supreme Overlord'}), field()],
  ['Sheer Force, no secondary effect', mon('Landorus', 'Timid', SPECIAL_ATTACKER, {ability: 'Sheer Force'}), 'Earthquake', mon('Incineroar', 'Careful', BULKY, {ability: 'Blaze'}), field()],
  ['Hustle', mon('Dragonite', 'Adamant', ATTACKER, {ability: 'Hustle'}), 'Extreme Speed', mon('Kingambit', 'Adamant', BULKY, {ability: 'Supreme Overlord'}), field()],
  ['Pure Power with Choice Band', mon('Medicham', 'Adamant', ATTACKER, {ability: 'Pure Power', item: 'Choice Band'}), 'Zen Headbutt', mon('Amoonguss', 'Bold', BULKY, {ability: 'Regenerator'}), field()],
  ['Rain, Water move', mon('Pelipper', 'Modest', SPECIAL_ATTACKER, {ability: 'Drizzle'}), 'Hydro Pump', mon('Incineroar', 'Careful', BULKY, {ability: 'Blaze'}), field({weather: 'Rain'})],
  ['Rain, Fire spread move', mon('Charizard', 'Timid', SPECIAL_ATTACKER, {ability: 'Blaze'}), 'Heat Wave', mon('Amoonguss', 'Bold', BULKY, {ability: 'Regenerator'}), field({weather: 'Rain'})],
  ['Sun, Water move', mon('Pelipper', 'Modest', SPECIAL_ATTACKER, {ability: 'Drizzle'}), 'Hydro Pump', mon('Incineroar', 'Careful', BULKY, {ability: 'Blaze'}), field({weather: 'Sun'})],
  ['Grassy Terrain', mon('Rillaboom', 'Adamant', ATTACKER, {ability: 'Grassy Surge'}), 'Wood Hammer', mon('Urshifu-Rapid-Strike', 'Adamant', BULKY, {ability: 'Unseen Fist'}), field({terrain: 'Grassy'})],
  ['Psychic Terrain', mon('Indeedee-F', 'Modest', SPECIAL_ATTACKER, {ability: 'Psychic Surge'}), 'Psychic', mon('Amoonguss', 'Bold', BULKY, {ability: 'Regenerator'}), field({terrain: 'Psychic'})],
  ['Electric Terrain, Flying user', mon('Zapdos', 'Timid', SPECIAL_ATTACKER, {ability: 'Static'}), 'Thunderbolt', mon('Pelipper', 'Bold', BULKY, {ability: 'Drizzle'}), field({terrain: 'Electric'})],
  ['Electric Terrain, Levitate user', mon('Rotom-Wash', 'Modest', SPECIAL_ATTACKER, {ability: 'Levitate'}), 'Thunderbolt', mon('Pelipper', 'Bold', BULKY, {ability: 'Drizzle'}), field({terrain: 'Electric'})],
  ['Technician 2-5 hits as 3', mon('Breloom', 'Adamant', ATTACKER, {ability: 'Technician'}), 'Bullet Seed', mon('Urshifu-Rapid-Strike', 'Adamant', BULKY, {ability: 'Unseen Fist'}), field()],
  ['Levitate immunity', mon('Garchomp', 'Jolly', ATTACKER, {ability: 'Rough Skin'}), 'Stomping Tantrum', mon('Rotom-Wash', 'Bold', BULKY, {ability: 'Levitate'}), field()],
  ['Water Absorb immunity', mon('Pelipper', 'Modest', SPECIAL_ATTACKER, {ability: 'Drizzle'}), 'Hydro Pump', mon('Vaporeon', 'Bold', BULKY, {ability: 'Water Absorb'}), field()],
  ['Multiscale below full HP', mon('Iron Bundle', 'Timid', SPECIAL_ATTACKER, {ability: 'Quark Drive'}), 'Ice Beam', mon('Dragonite', 'Adamant', BULKY, {ability: 'Multiscale', current_hp: 150}), field()],
  ['Shadow Shield', mon('Flutter Mane', 'Timid', SPECIAL_ATTACKER, {ability: 'Protosynthesis'}), 'Shadow Ball', mon('Lunala', 'Bold', BULKY, {ability: 'Shadow Shield'}), field()],
  ['Multiscale on the first of two hits', mon('Dragapult', 'Jolly', ATTACKER, {ability: 'Clear Body'}), 'Dragon Darts', mon('Dragonite', 'Adamant', BULKY, {ability: 'Multiscale'}), field()],
  ['Ten hits', mon('Maushold', 'Jolly', ATTACKER, {ability: 'Technician'}), 'Population Bomb', mon('Amoonguss', 'Bold', BULKY, {ability: 'Regenerator'}), field()],
  ['Always-crit ignores Reflect', mon('Urshifu-Rapid-Strike', 'Adamant', ATTACKER, {ability: 'Unseen Fist'}), 'Surging Strikes', mon('Incineroar', 'Careful', BULKY, {ability: 'Blaze'}), field({reflect: true})],
  ['Always-crit ignores -2 Attack', mon('Urshifu', 'Adamant', ATTACKER, {ability: 'Unseen Fist', boosts: {atk: -2}}), 'Wicked Blow', mon('Gholdengo', 'Modest', BULKY, {ability: 'Good as Gold'}), field()],
  ['+2 Attack into -1 Defense', mon('Urshifu', 'Adamant', ATTACKER, {ability: 'Unseen Fist', boosts: {atk: 2}}), 'Close Combat', mon('Incineroar', 'Careful', BULKY, {ability: 'Blaze', boosts: {def: -1}}), field()],
  ['Shedinja has 1 HP', mon('Garchomp', 'Jolly', ATTACKER, {ability: 'Rough Skin'}), 'Rock Slide', mon('Shedinja', 'Adamant', BULKY, {ability: 'Wonder Guard'}), field()],
  ['Type-boosting item, special', mon('Charizard', 'Timid', SPECIAL_ATTACKER, {ability: 'Blaze', item: 'Charcoal'}), 'Flamethrower', mon('Amoonguss', 'Bold', BULKY, {ability: 'Regenerator'}), field()],
  ['Level 100', mon('Garchomp', 'Jolly', ATTACKER, {ability: 'Rough Skin', level: 100}), 'Dragon Claw', mon('Dragonite', 'Adamant', BULKY, {ability: 'Inner Focus', level: 100}), field()],
].map(([name, attacker, move, defender, conditions]) => ({name, attacker, move, defender, field: conditions}));

function calcPokemon(gen, set) {
  return new calc.Pokemon(gen, set.species, {
    level: set.level || LEVEL,
    nature: set.nature,
    evs: set.evs,
    item: set.item || undefined,
    ability: set.ability || undefined,
    status: set.status || '',
    boosts: set.boosts,
    curHP: set.current_hp || undefined,
  });
}

function calcField(set) {
  return new calc.Field({
    gameType: GAME_TYPE,
    weather: set.weather || undefined,
    terrain: set.terrain || undefined,
    defenderSide: {isReflect: set.reflect, isLightScreen: set.light_screen},
  });
}

function rollsPerHit(damage) {
  if (typeof damage === 'number') return [[damage]];
  return typeof damage[0] === 'number' ? [damage] : damage;
}

// In doubles the calculator reduces every move of these targets as a spread move; the fixture says so per case.
const SPREAD_TARGETS = ['allAdjacent', 'allAdjacentFoes'];

const gen = calc.Generations.get(GENERATION);

function computed(reference) {
  const defender = calcPokemon(gen, reference.defender);
  const move = new calc.Move(gen, reference.move);
  const result = calc.calculate(gen, calcPokemon(gen, reference.attacker), defender, move, calcField(reference.field));
  const [min, max] = result.range();
  return {
    ...reference,
    field: {...reference.field, spread: SPREAD_TARGETS.includes(move.target)},
    expected: {min, max, defender_max_hp: defender.maxHP(), rolls: rollsPerHit(result.damage)},
  };
}

const cases = CASES.map(computed);
const modifierCases = MODIFIER_CASES.map(computed);
const fixture = {
  calculator: '@smogon/calc',
  calc_version: calcVersion,
  generation: GENERATION,
  level: LEVEL,
  game_type: GAME_TYPE,
  cases,
  modifier_cases: modifierCases,
};
fs.writeFileSync(outputPath, JSON.stringify(fixture, null, 2) + '\n');
console.log(`wrote ${cases.length} cases and ${modifierCases.length} modifier cases from @smogon/calc ${calcVersion} to ${outputPath}`);
