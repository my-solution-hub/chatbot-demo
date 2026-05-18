# Branching and Delivery Policy

This repository protects the `main` branch. Every change — code, tests, docs, specs, hooks, workflow files — must arrive on `main` through a pull request from a feature or bugfix branch.

## Rules

1. **Never commit directly to `main`.** Do not push to `main`. Do not amend, rebase, or fast-forward `main` from a local checkout. The only commits that land on `main` are merge or squash-merge commits produced by an approved pull request.

2. **Branch naming.**
   - New functionality: `feat/<short-kebab-summary>` (e.g. `feat/wake-word-support`)
   - Bug fixes: `bugfix/<short-kebab-summary>` (e.g. `bugfix/echo-gate-warming`)
   - Documentation-only updates may use `docs/<...>` and follow the same PR flow.

3. **Pull request flow.**
   - Branch off the latest `main`.
   - Push the branch to `origin` with `git push -u origin <branch-name>`.
   - Open a PR targeting `main` (e.g. `gh pr create --base main`).
   - Wait for the `tests` GitHub Actions workflow to pass on the PR before requesting review or merging.
   - Prefer squash-merge so `main` keeps a linear, readable history.

4. **CI is required.** The workflow at `.github/workflows/test.yml` runs `pytest -q` on every PR. Do not merge a PR with failing or skipped CI.

5. **Local verification before pushing.** Run the full suite (`pytest -q`) locally before opening or updating a PR. New behaviour requires new tests in `tests/`. Property-based tests live in `tests/test_properties.py`.

## What this means for the agent

- When asked to implement a feature, fix a bug, or update docs, create a branch first and commit there. Never run `git commit` while `HEAD` is on `main`.
- If `git status` shows the current branch is `main`, stop and create a branch (`git checkout -b feat/...` or `bugfix/...`) before staging any change.
- **Always branch from the latest `main`.** Before creating a new branch, run `git checkout main && git pull origin main` first. Never branch from another feature/docs branch.
- When asked to push, push to the feature/bugfix branch with `-u`, then create or update the PR. Never use `git push origin main` or `git push --force` against `main`.
- If a change spans multiple concerns, split it into sequential PRs from sequential branches rather than one large branch.
- Always use `--no-verify` when pushing (e.g. `git push --no-verify -u origin <branch-name>`) to bypass the pre-push hook.
- **Include all modified files in the commit.** Before committing, run `git status` to verify all intended changes are staged. Do not leave modified tracked files uncommitted.
