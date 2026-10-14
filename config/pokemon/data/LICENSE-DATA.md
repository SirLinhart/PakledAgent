# Pokémon game data: source and license

The JSON files in this folder are written by `scripts/build_pokemon_data.py`
from the static data of poke-env, folder `src/poke_env/data/static/` of
https://github.com/hsahovic/poke-env, which poke-env generates from the data of
Pokémon Showdown, https://github.com/smogon/pokemon-showdown.
Both projects are under the MIT license, reproduced below. Only the fields
the agent uses are kept, and the agent reads only these files.

| Source file | SHA-256 |
| --- | --- |
| `pokedex/gen9pokedex.json` | `6a1d6afed1bf5e4c651bf2a39bf4585d38d93bf824a14453d5bcfe0b980098e2` |
| `moves/gen9moves.json` | `3a712e0fcf2f9da0a382a337ea35e8c72aebb7d5a81933e33ed7fc00de16b3f1` |
| `typechart/gen9typechart.json` | `1d53f6f6593b6fd7370d6e8949e3c2c9c778350c3ca32069296129f93b3f2680` |
| `natures.json` | `24b422cfd95423858486ab2b35f3c5fed6746e33b2d7b77ad6eea2d9ea57c6c5` |

## poke-env

MIT License

Copyright (c) 2019 hsahovic

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Pokémon Showdown

The MIT License (MIT)

Copyright (c) 2011-2026 Guangcong Luo and other contributors http://pokemonshowdown.com/

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
