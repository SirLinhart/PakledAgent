# Upstream source of `src/altruagent/`

This folder is a verbatim copy of the `altruagent` package from the official
AltruAgent starter kit. Nothing in it is edited here; behavior the agent needs
beyond it lives in `src/ucla_agent/` and `src/agent/`.

| | |
|---|---|
| Source | https://github.com/UCLA-Trustworthy-AI-Lab/altruagent-starter |
| Commit | `951f4f74b008c6aa7f40bec09e741aa67d3c45cc` (2026-10-07, "Merge pull request #7 from wu-warren/fix/runtime-resilience"), the head of `origin/main` when copied |
| Path in the source | `altruagent/` |
| Copied on | 2026-10-10 |
| Files copied | the 17 `.py` files listed in the manifest below (the package holds no other file) |

## License

The upstream repository has no license at this commit: it contains no
`LICENSE`, `COPYING` or `NOTICE` file, its `pyproject.toml` declares no
license, and its `README.md` and `GAMES.md` do not mention one. There is
therefore no upstream license text to reproduce here. The package is the
starter kit the tournament organizers publish for contestants to build their
agents on, and it is used here for that purpose.

## How the copy was made

Each file was written from the git object of the commit above
(`git cat-file blob <commit>:altruagent/<file>`, the same bytes as
`git show <commit>:altruagent/<file>`), not from a working tree, so
the bytes are the ones stored upstream (LF line endings). `.gitattributes` in
the repository root turns off line-ending conversion for this folder so that
every checkout keeps those bytes.

## Manifest

SHA-256 of every `.py` file copied, as `<sha256>  <file name>`:

```
beef58fcd51ee785601102172e047b2e4256a97cbf2325e76adae47c73e58f4b  __init__.py
c637f615b10fecc385a43ccf752c582591f9d0adab5628aa8725f1eac2859abb  _responses.py
e4909a4df0748404e3505769aa1ddb142148a03af6c0c4027e1851721d14fe41  agent_loader.py
ee739ee3ef3366aaafe79b3cd57c78c6b45466ba4ddd6330c1c5096531bf703e  auth.py
a99551c291254cd55ab2f49f2c66c3318194572bd41f5bf36ca5c68b101dfa8a  client.py
ca77b99fd349af1b627888c627989f6c15219a551c3df53e66087c214dfe21a8  console.py
a49ccba38f468333dc15b1419f626bed22a24c1cd8cca32eaa33e536873da9ec  errors.py
dee97503cad19af9e5a2cfbbccaedc73b21916a24646c451ddc2d585db1acbdd  game.py
6f3968a8f9208928cf16f4b5b56e43b425511fc7b54b925f6f5fa0937b437dbb  mcp_game.py
d3fdcf5ea9b615e5194f4e5028c8e8f4f9f9ddabb7ae1c086568888660ec1bff  mcp_transport.py
ba7a3411ac53f5831185d308f550ed5afe2ef346d24746837ab6118287aab8a1  models.py
f803c6ad8bca054b4a9f65f1eb8f16782d572c3300cfb497e8debb9527aa69cd  notices.py
8562dab0be39708995700484e0c924ab4110d1f5f52e7e2404389852b85b30e0  official.py
29f45f8fdc4757551ac54c828756e3321df05a4ded5dc1db5af741239b9b736a  runner.py
2f14b54f11f2365ab089098ab396bd072531fc3cf215e521270f1d47fd8e5756  runtime.py
ad6cdeba2b64347da96f5646e5493f86b01460d0e0c76e1372f5b0703bf374d8  supervisor.py
c1fc1678c50f169c6b56f7529acc5b327d84ec18b8bdb0731b4e87a4dc308b7a  worker.py
```

To check a checkout against the manifest, run `pytest tests/test_layout.py`.
