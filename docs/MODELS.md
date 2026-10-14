# Local models

The agent can ask a language model that runs on the same machine to choose
between options a game's script has prepared. Those models are run by the
model server included in this repository (`src/ucla_agent/core/modelserver/`):
it starts one `llama-server` process per *role*, each with one model file, and
serves all of them through one HTTP endpoint on the loopback interface. Game
code reaches a local model only through that endpoint and names the role it
wants, never a model file.

Every setting of the model server and of each model is in the tracked file
`config/models.toml`. Nothing is set anywhere else, and the agent only reads
that file. Each run records the file's SHA-256 (`config_hashes` in
`runs/<UTC yyyymmdd>/run.jsonl` and in every match's `meta.json`), and each
match's `meta.json` lists the roles with their model file names and settings.

The model server never downloads anything. Each model file has to be at the
path its role gives. A role whose file is missing is reported as down and the
agent plays without it.

## Roles

### `fast`

| | |
|---|---|
| Model file | `gemma-4-26B-A4B-it-qat-heretic-UD-Q4_K_XL.gguf` |
| Model | Gemma 4 26B A4B, instruction-tuned, quantization-aware-trained weights, "heretic" (decensored) variant, GGUF quantization `UD-Q4_K_XL`. The file names its base model as [google/gemma-4-26B-A4B-it-qat-q4_0-unquantized](https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-unquantized). |
| Source | <https://huggingface.co/SC117/gemma-4-26B-A4B-it-qat-heretic-GGUF>, revision `82c751632a54978819dda9b95028dc4fcd117ece` |
| Download | <https://huggingface.co/SC117/gemma-4-26B-A4B-it-qat-heretic-GGUF/resolve/82c751632a54978819dda9b95028dc4fcd117ece/gemma-4-26B-A4B-it-qat-heretic-UD-Q4_K_XL.gguf> |
| Size | 14,249,045,568 bytes (13.27 GiB) |
| SHA-256 | `2f18525262c8c914cefffa79a0418188167daf9e841a5a315af4361e9e81f7fd` |

Settings (`[roles.fast]` in `config/models.toml`):

| Setting | Value |
|---|---|
| `model_path` | `"E:/Models/gguf/gemma-4-26B-A4B-it-qat-heretic-UD-Q4_K_XL.gguf"` |
| `required` | `false` |
| `ctx_size` | `8192` |
| `n_gpu_layers` | `999` |
| `parallel` | `1` |
| `temperature` | `0` |
| `top_k` | `64` |
| `top_p` | `0.95` |
| `seed` | `0` |
| `reasoning` | `"off"` |
| `extra_args` | `["--no-mmap"]` |

Command line the model server starts it with:

```
C:/Tools/llama.cpp/llama-server.exe --model E:/Models/gguf/gemma-4-26B-A4B-it-qat-heretic-UD-Q4_K_XL.gguf --host 127.0.0.1 --port 41500 --ctx-size 8192 --n-gpu-layers 999 --parallel 1 --temp 0 --top-k 64 --top-p 0.95 --seed 0 --reasoning off --no-mmap
```

### `strong`

| | |
|---|---|
| Model file | `Qwen3.8-27B-Q8_0.gguf` |
| Model | Qwen3.8 27B, GGUF quantization `Q8_0`. The model's publisher page is [Qwen/Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B). |
| Source | <https://huggingface.co/bartowski/Qwen3.8-27B-GGUF>, revision `f0eec4a4bb4975114a030d048952d83c0a53c034` |
| Download | <https://huggingface.co/bartowski/Qwen3.8-27B-GGUF/resolve/f0eec4a4bb4975114a030d048952d83c0a53c034/Qwen3.8-27B-Q8_0.gguf> |
| Size | 29,116,388,960 bytes (27.12 GiB) |
| SHA-256 | `a830e736efa71ce29589ff20b8713333a427e0d46ab774723be23800ad54362d` |

Settings (`[roles.strong]` in `config/models.toml`):

| Setting | Value |
|---|---|
| `model_path` | `"E:/Models/gguf/Qwen3.8-27B-Q8_0.gguf"` |
| `required` | `false` |
| `ctx_size` | `16384` |
| `n_gpu_layers` | `999` |
| `parallel` | `1` |
| `temperature` | `0` |
| `top_k` | `20` |
| `top_p` | `0.95` |
| `seed` | `0` |
| `reasoning` | `"off"` |
| `extra_args` | `["--no-mmap"]` |

Command line the model server starts it with:

```
C:/Tools/llama.cpp/llama-server.exe --model E:/Models/gguf/Qwen3.8-27B-Q8_0.gguf --host 127.0.0.1 --port 41501 --ctx-size 16384 --n-gpu-layers 999 --parallel 1 --temp 0 --top-k 20 --top-p 0.95 --seed 0 --reasoning off --no-mmap
```

### `small`

| | |
|---|---|
| Model file | `Qwen3.5-4B-Q8_0.gguf` |
| Model | Qwen3.5 4B, GGUF quantization `Q8_0`, quantized by Unsloth. The file names its base model as [Qwen/Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B). |
| Source | <https://huggingface.co/unsloth/Qwen3.5-4B-GGUF>, revision `e87f176479d0855a907a41277aca2f8ee7a09523` |
| Download | <https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-Q8_0.gguf> |
| Size | 4,482,403,488 bytes (4.17 GiB) |
| SHA-256 | `10cc391b403021dd11c614679d2fd92f611c3681d29e29651b717316965d61e1` |

Settings (`[roles.small]` in `config/models.toml`):

| Setting | Value |
|---|---|
| `model_path` | `"C:/Models/gguf/Qwen3.5-4B-Q8_0.gguf"` |
| `required` | `false` |
| `ctx_size` | `8192` |
| `n_gpu_layers` | `999` |
| `parallel` | `1` |
| `temperature` | `0` |
| `top_k` | `20` |
| `top_p` | `0.8` |
| `seed` | `0` |
| `reasoning` | `"off"` |
| `extra_args` | `["--no-mmap"]` |

Command line the model server starts it with:

```
C:/Tools/llama.cpp/llama-server.exe --model C:/Models/gguf/Qwen3.5-4B-Q8_0.gguf --host 127.0.0.1 --port 41502 --ctx-size 8192 --n-gpu-layers 999 --parallel 1 --temp 0 --top-k 20 --top-p 0.8 --seed 0 --reasoning off --no-mmap
```

`small` is defined here but not used as shipped: `autostart_roles` does not list it and no chain of `config/agent.toml` asks it; Red Alert's chains ask JevK5-9B (see [JevK5-9B, the systemone role `jev9b`](#jevk5-9b-the-systemone-role-jev9b)). When it answered Red Alert, in the watched game `20261013T173137Z` (England, won by elimination at 15:24), its 1,983 choices, most of them commander questions, took a median of 144 ms and at most 507 ms.

Plan-choice latency, measured on 2026-10-07 on the development workstation
(NVIDIA RTX PRO 6000 Blackwell, 96 GB) with
`pytest -m gpu tests/core/test_modelserver_gpu.py -k small`: 20 Red Alert
plan choices (the standard fake scenario at 300 s, HOLD and RAID open) put
through `local:small` with the 1,500 ms deadline, `small` the only role
loaded: p50 76 ms, p95 95 ms, 0 fallbacks. All 20 are the same question;
the first took 123 ms.

With a short reason asked for as well (`choice_reasons = true` in
`[decide]`, as shipped), a choice may take up to 64 tokens instead of 24.
`pytest -m gpu tests/core/test_modelserver_gpu.py -k reason` puts the same
20 plan choices to `small` with reasons on and requires every answer to be
used with a reason, a median latency of at most 600 ms and none slower than
1,200 ms.

The sizes and SHA-256 values above are the ones the source repositories
publish for these files at the revisions named; those of `small` were also
measured on the downloaded file.

## What each role setting does

Each setting becomes one argument of the role's `llama-server`, in the order
shown in the command lines above (`src/ucla_agent/core/modelserver/command.py`).

| Setting | Argument | Meaning |
|---|---|---|
| `model_path` | `--model` | The model file. |
| `required` | none | When `true`, the agent does not start unless this role is ready. When `false`, a role that cannot be brought up is down and the agent plays without it. |
| `ctx_size` | `--ctx-size` | Context length in tokens. |
| `n_gpu_layers` | `--n-gpu-layers` | Layers kept on the GPU; `999` means all of them. Default `999`. |
| `parallel` | `--parallel` | Requests the server works on at the same time. Default `1`. |
| `temperature` | `--temp` | Sampling temperature a request gets when it sends none. `0` always takes the most likely token. |
| `top_k`, `top_p` | `--top-k`, `--top-p` | Sampling limits a request gets when it sends none. Left out of the file, the argument is left out too. The shipped values of `fast` and `strong` are the ones their model files carry as their own defaults; the `small` file carries none, and its values are the ones its publisher recommends for answers without reasoning. |
| `seed` | `--seed` | Seed of the sampler's random numbers. Left out of the file, the argument is left out too. |
| `reasoning` | `--reasoning` | `"off"` or `"on"`: whether the model writes out a reasoning passage before its answer. |
| `extra_args` | appended as they are | Further `llama-server` arguments, after all the others. |

`--host 127.0.0.1` and the role's `--port` are always added.

Every shipped role has `extra_args = ["--no-mmap"]`, so `llama-server` reads
the model into VRAM instead of mapping the model file into memory. A mapped
file stays resident in system RAM next to its copy in VRAM, so the model
takes its size twice. Measured on 2026-10-09 with the `strong` role:
memory-mapped, 26.5 GB of the 27B model stayed resident in system RAM beside
30.2 GB in VRAM, and free RAM fell from 41.3 GB to 14.9 GB; with `--no-mmap`
the process held 1.8 GB of RAM with 30.3 GB in VRAM, 39.3 GB of RAM stayed
free, and the first answer took 245 ms.

## Server settings

`[server]` in `config/models.toml`:

| Setting | Value | Meaning |
|---|---|---|
| `host` | `"127.0.0.1"` | Address of the endpoint. It must be an IPv4 loopback address: the server is never reachable from another machine. |
| `port` | `11500` | Port of the endpoint, `http://127.0.0.1:11500`. |
| `llama_server_path` | `"C:/Tools/llama.cpp/llama-server.exe"` | The `llama-server` program ([llama.cpp](https://github.com/ggml-org/llama.cpp)). The agent was developed with build 9225 (commit `0b321715f`). |
| `backend_port_base` | `41500` | Role number *i*, counted from 0 in the order of the file, is served by a `llama-server` on port `backend_port_base + i`: `fast` on 41500, `strong` on 41501, `small` on 41502. The systemone roles come after them: `jev9b` on 41503. |

`[courtesy]` in `config/models.toml`:

| Setting | Value | Meaning |
|---|---|---|
| `modelbouncer_lease` | `true` | Some workstations run a service called ModelBouncer on `http://127.0.0.1:11435` that hands out GPU memory. When it answers within one second, the model server tells it how much memory the models are about to take (the sizes of the model files plus 4,096 MiB; 49,728 MiB for the three shipped roles). Nothing depends on the answer, and where no such service runs nothing is sent. |

## How the model server runs

The model server is started and stopped together with the agent when
`config/agent.toml` has `autostart = true` in its `[models]` table, with only
the roles its `autostart_roles` lists. As shipped that is `strong` and `jev9b`:
`strong`, the role the Werewolf and Pokémon chains ask, takes about 30 GB of
VRAM and about 2 GB of RAM, and `jev9b`, the systemone role Red Alert's chains
ask, about 23 GB of VRAM (see below), about 53 GB of VRAM together, which fits
the development workstation's 96 GB card. `fast` and `small` are not loaded, and a choice that would have
gone to a role that is not running is made by the game's own script instead. A
role the list names that `config/models.toml` does not have stops the agent
at startup with a configuration error; an empty list, or `autostart = false`,
starts no model.

At start the server launches the roles one after another. For each it checks
that the model file exists and the port is free, starts `llama-server`, waits
up to 180 seconds for its `/health` to report ready and sends one 1-token
completion to warm it up. It prints one line per role saying whether the role
is ready or down and why. A role that is down stays down for that run.

The endpoint serves:

- `POST /v1/chat/completions`: the OpenAI chat format, not streamed. The
  `model` field is a role name. The request goes to that role's
  `llama-server` unchanged, so `response_format` (a JSON schema) and `grammar`
  work as they do there. An unknown role is answered with HTTP 404
  `{"error": "unknown_role"}` and a role that is down with HTTP 503
  `{"error": "role_down"}`.
- `GET /v1/models`: the role names.
- `GET /health`: each role's state, `ready` or `down`, with the reason for a
  down one.

Every `llama-server` is tied to the agent's process by the operating system
(a job object on Windows, a parent-death signal on Linux): when the agent
stops or is killed, the servers end with it. Each server's own output is
appended to `runs/model-server/<role>.log`.

## JevK5-9B, the systemone role `jev9b`

As shipped, Red Alert's plan, scout and commander chains ask JevK5-9B, and
the agent starts it beside `strong`. If it does not start, or stops
answering, the script plays Red Alert: each choice it would have made is the
script's own, through the usual deadline and circuit breaker, and no chain
goes on to another model. The 4B (`small`) is defined but not used as shipped.
In the watched game `20261014T015507Z` (England against normal on sidestep,
won on time, value traded +51,200) all of its 2,278 choices were used, with no
model error, in a median of 187 ms and at most 588 ms under concurrent load.

JevK5-9B (v0.3.3, by alibiserikbay, Apache-2.0) is a typed-decision model:
given a situation and a choice question, one forward pass gives a calibrated
probability for every option. It is not a GGUF model and `llama-server`
cannot run it, so it is a role of another kind, a *systemone role*: a native
server of its own, on its own port, asked over the systemone API
(`POST /v1/systemone`) rather than through the endpoint. The endpoint lists
it in `/health` and `/v1/models` but sends it no chat completion.

### What it needs

None of this is part of the repository, and the agent downloads nothing. The
setup is the one of the JevTest project (`C:/Projects/JevTest` on the
development workstation): its `serve-native-cascade.py` and
`Start-Native-JevCascade.ps1`, and the jev-style README it keeps in
`C:/Projects/JevTest/jev-bench/native-runtime-review`.

| | |
|---|---|
| Runtime | A native Windows Python environment with CUDA PyTorch and the two packages that run the model: [jev-style](https://github.com/lawrence3699/jev-style) 0.4.0 and its in-process JevK5 engine, and [allebee/jevk5](https://github.com/allebee/jevk5) 0.3.3 (Apache-2.0), which is not on PyPI: `pip install "jev-style[torch]" "jevk5[fast] @ git+https://github.com/allebee/jevk5@v0.3.3"`. The development workstation uses JevTest's environment `C:/Projects/JevTest/jev-bench/.native-win`: torch 2.14.1+cu130, triton-windows 3.8.0.post29, transformers 5.18.0, flash-linear-attention 0.5.2. |
| Weights | [alibiserikbay/JevK5-9B](https://huggingface.co/alibiserikbay/JevK5-9B), revision `d6521a18a86999190e9d775c915af3d6d6772fc4` (the one jev-style pins), as a folder with its `SHA256SUMS` and `jevk5_config.json` (its calibration temperatures; JevK5 is not loaded without it). `jev-style download --cascade cascade-9b` fetches it. Its model card says part of its training labels came from OpenAI's GPT-6 Luna, under OpenAI's terms. |
| GPU | An NVIDIA GPU with CUDA; there is no CPU fallback. |

### Settings

`[systemone_roles.jev9b]` in `config/models.toml`, which is the only place
machine paths are written:

| Setting | Value | Meaning |
|---|---|---|
| `python_path` | `"C:/Projects/JevTest/jev-bench/.native-win/Scripts/python.exe"` | The Python of the native environment. |
| `script` | `"scripts/serve_jev9b.py"` | The serve script, from the repository root. |
| `model_dir` | `"E:/Models/JevK5-9B"` | The JevK5-9B folder. |
| `required` | `false` | As for a llama role. |
| `startup_seconds` | `600` | How long the server may take to answer `/healthz` before the role is down. Default `600`. |
| `env` | `PYTORCH_CUDA_ALLOC_CONF`, `HF_HOME`, `TRITON_CACHE_DIR`, `JEVK5_GRAPHS` | Added to the server's environment: the allocator without expandable segments, the Hugging Face home, a Triton kernel cache of its own and the CUDA graphs on, as JevTest sets them. |

The systemone roles are numbered after the llama roles, so `jev9b` is
served on `backend_port_base + 3`: 41503, and 41603 in a watched game. The
live agent holds it beside 11500 and 41500 to 41502; it is not JevTest's own
server's port (8765).

`scripts/serve_jev9b.py` follows JevTest's `serve-native-cascade.py`, which
serves the cascade `cascade-9b` (the Jev-Style 2B, then JevK5-9B), but serves
the JevK5-9B tier alone, with no 2B. jev-style refuses a cascade of one tier,
so the script builds the tier's engine the way jev-style builds a
`jevk5:<folder>` tier (its `SHA256SUMS` checked, the calibration file
required, CUDA graphs recorded) and serves it with jev-style's own app. It
holds no machine path: the model folder and port come on its command line,
the rest from the role's `env`.

### How it starts and is asked

The model server starts `jev9b` only when `autostart_roles` lists it. It
checks that the Python, the script and the model folder exist (a missing one
leaves the role down with a reason naming the path, and the game plays on
the script's choices) and that the port is free, then runs
`<python_path> scripts/serve_jev9b.py --model-dir <model_dir> --host 127.0.0.1 --port 41503`.
The server listens only once the model is loaded; the role counts as ready
after `GET /healthz` answers and one warm-up choice question has been
answered, so the CUDA graphs are recorded before the first real question.
It is stopped with the model server, and its output goes to
`runs/model-server/jev9b.log`. `python -m ucla_agent.check` asks an
autostarted `jev9b` for its `/healthz` and notes, without failing, a port
that was already in use. With the model folder (`E:/Models/JevK5-9B` as
shipped) or the Python of JevTest's `.native-win` environment missing, its
`model_server` line reports `jev9b` as not answering `/healthz`, a `WARN`,
since the role is optional and the game plays on the script's choices
without it; the line the model server prints for the role at startup names
the missing path. The tests never start the 9B.

A chain asks it as `"systemone:jev9b"`. A question goes out as one choice
question: the request's context as the `state`, its system text and question
as the `instructions`, and the options, once, as the `criteria`. The answer's
choice and confidence (0 when the probabilities are even, 1 when one option
takes them all) are read; JevK5 writes no reason. `[systemone.jev9b]` of
`config/agent.toml` sets `min_confidence` (shipped `0.0`): a less confident
answer is not used. A timeout, an error status such as 422, an unreadable
answer or an option that was not offered also leaves the script's own choice,
as for every source.

### VRAM and latency

Measured on 2026-10-13 on the development workstation (NVIDIA RTX PRO 6000
Blackwell, 96 GB), under concurrent GPU load: JevTest's own server was
resident and other JevK5-9B tests may have shared the GPU, so the times may
be inflated and the GPU-wide memory figure is approximate. Started alone
from this repository's model server, `jev9b` was ready in 69.6 s (the
script's own 67.0 s for the hash check of the weights, the load and 13 CUDA
graphs); PyTorch reserved 20.1 GiB in the server, and the GPU's memory in
use rose by 22.9 GB while it ran. With `strong`, about 30 GB, that is about
53 GB of VRAM together, against about 35 GB for `strong` and `small`. The
snapshot scene's commander question (six options, 2,781 characters, 1,213
input tokens) asked 20 times through `systemone:jev9b` was answered `H`
every time, with confidence 0.54, in a median of 157 ms and at most 180 ms,
within its 800 ms deadline. JevTest measured the 9B in-process, without
HTTP, at a median of 47.86 ms on a compact state and 247.07 ms on a full one.

### The shipped lines

The engine is set in `config/agent.toml` before the agent starts. As
shipped:

```toml
[models]
autostart_roles = ["strong", "jev9b"]

[decide.chains]
red_alert_plan = ["systemone:jev9b"]
red_alert_scout = ["systemone:jev9b"]
red_alert_commander = ["systemone:jev9b"]
```

`red_alert_scout` is asked only when `model_scout_choice` is on. The 4B is
selected by listing `small` in place of `jev9b` and setting these chains to
`["local:small"]`. For a watched game, `python scripts/watch_game.py --model
jev9b` makes the same choice in the game's snapshot alone.

## Checking a model by hand

```
pytest -m gpu tests/core/test_modelserver.py
```

This starts the real `llama-server` with the shipped `fast` role, asks it for
one completion and stops it again. It needs the model file, the GPU and the
ports 11500 and 41500 free, and it is not part of the default test run.

```
pytest -m gpu tests/core/test_modelserver_gpu.py -k small
```

This starts the shipped `small` role alone, puts the Red Alert plan choice to
it 20 times and prints the p50 and p95 latency and the number of fallbacks
(answers the plan selector would not take). It needs the same as the test
above, with the `small` model file.

```
pytest -m gpu tests/core/test_modelserver_gpu.py -k reason
```

This does the same with a short reason asked for with each choice, prints
each answer with its reason and latency, and checks that all 20 are used,
each with a reason, with a median latency of at most 600 ms and the slowest
at most 1,200 ms. It needs the same as the test above.

```
pytest -m gpu tests/core/test_modelserver_gpu.py -k no_mmap
```

This starts the shipped `strong` role alone, as the agent's autostart does,
asks it one completion and checks that its `llama-server` holds less than
4 GB of system memory from the moment it is ready until it has answered, and
that the answer comes within 30 seconds. It needs the `strong` model file, the
GPU and the ports 11500 and 41501 free. Measured on 2026-10-09 on the
development workstation: at most 2.26 GB resident (2.26 GB at the process's
peak), the answer in 312 ms.
