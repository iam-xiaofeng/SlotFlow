# Development Notes

All development happens inside WSL.

```txt
workspace: /home/dell/code/SlotFlow
reference repo: /mnt/d/test/deer-flow
```

Do not install Python virtual environments or `node_modules` under `/mnt/c` or
`/mnt/d`. Those paths are Windows-mounted filesystems and are slower and more
error-prone for Linux tooling.

## Verified Commands

Backend:

```bash
cd ~/code/SlotFlow/backend
uv run pytest -q
```

Frontend:

```bash
cd ~/code/SlotFlow/frontend
pnpm install
pnpm typecheck
pnpm build
```

Repository-wide:

```bash
cd ~/code/SlotFlow
make verify
```
