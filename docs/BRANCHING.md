# How we work with branches (a beginner's guide)

New to Git branches? Read this once and keep the cheat sheet at the bottom handy.

## The idea in one picture

```
feature branch  ──►  dev  ──►  main
(your work)          (try it)   (the real thing)
```

| Branch | What it is | What happens automatically |
|---|---|---|
| `main` | The code that is **live** for the other teams. Never work on it directly. | Checks run. Later: automatic, careful rollout to production. |
| `dev` | The **testing ground**. Finished work lands here first. | A throwaway copy of the whole app is built on Azure and tested, then stays up for 10 minutes so you can try it yourself. |
| feature branch | Your own copy of `dev` for one piece of work, such as `feat/checkout-button`. | Checks run when you open a pull request. |

Nobody pushes straight to `main` or `dev`. Everything goes through a **pull request** (PR): a
page on GitHub that says "please merge my branch into that one". Checks run on the PR, and the
merge button only turns green when they pass.

## Step by step

### 1. Start from an up-to-date `dev`
```
git checkout dev
git pull
git checkout -b feat/short-description
```
Branch names are free-form; `feat/...`, `fix/...` and `docs/...` keep things tidy.

### 2. Make your changes and commit them
```
git add <the files you changed>
git commit -m "feat: Add the checkout button"
```
Commit messages must look like `type: Subject`.

- **type** is lowercase and one of: `feat` (new feature), `fix` (bug fix), `docs`, `refactor`,
  `test`, `ci`, `chore`, `perf`, `build`, `style`, `revert`.
- An optional scope goes in brackets: `fix(api): Handle a missing hardware id`.
- After the colon and a space, write the subject any way you like. Capital letters are
  allowed anywhere: `feat: Add gRPC health check` is fine.
- Keep the first line under 100 characters.

Good: `fix: Reject returns larger than the outstanding quantity`
Rejected: `Fixed stuff` (no type), `Feat: add thing` (capital type), `feat:add thing` (no space).

### 3. Push and open a pull request into `dev`
```
git push -u origin feat/short-description
```
Open GitHub, click **Compare & pull request**, and make sure the base branch says **`dev`**.
The PR title must follow the same `type: Subject` rule, because it becomes the commit message.

### 4. Wait for the checks
Under the PR you will see the checks. All must be green:

- **verify**: linting, security audit and the full test suite.
- **commitlint**: your commit messages and PR title are in the right format.
- **branch-flow**: only matters for PRs into `main` (see step 7).

A red check? Click **Details**, read the error, fix it, commit and push again. The PR updates
itself.

### 5. Merge into `dev`
Choose **Squash and merge**. That combines your commits into one tidy commit on `dev`.
Then delete the branch when GitHub offers.

### 6. Watch the dev preview and test by hand
A merge into `dev` triggers the **deploy-dev** workflow (GitHub → **Actions** tab) when automatic
previews are switched on. They are on while the repository variable `AUTO_DEPLOY_DEV` is `true`
(Settings → Secrets and variables → Actions → **Variables** tab). Pushes that only change
documentation (`*.md`, `docs/`) never deploy. To deploy while the switch is off, run it by hand:
Actions → deploy-dev → **Run workflow**.

When it runs, it:

1. Builds the two images.
2. Creates a brand-new copy of the app on Azure with its own empty database.
3. Runs the automated tests against it.
4. **Keeps it running for 10 minutes** so you can open the website link and try things yourself.
   The link is printed in the run's log. Sign in with your `DEV_PREVIEW_LOGIN_CODE`.
5. Deletes the copy and its database.

Need longer? Run **deploy-dev** by hand (Actions → deploy-dev → *Run workflow*) and raise
`hold_minutes`, or tick `keep` and remove it later with **destroy-dev-preview**.
Done early? Cancel the run and the copy is deleted straight away.

### 7. Release to production: a PR from `dev` into `main`
When `dev` is working well, open a PR with base **`main`** and compare **`dev`**.
Only `dev` may be merged into `main`; the **branch-flow** check blocks anything else.
Use **Create a merge commit** (not squash) for this one. Squashing `dev` into `main` makes the
two branches drift apart and causes confusing conflicts next time.

### 8. After a release, nothing else is needed
`main` and `dev` now match. Start the next piece of work from step 1.

## Urgent fix for production (hotfix)
Still go through `dev`: branch from `dev`, fix, PR into `dev`, check the preview, then PR
`dev` into `main`. It takes a few minutes longer and keeps the history simple.

## Cheat sheet
```
git checkout dev && git pull           # update dev
git checkout -b fix/my-fix             # new branch
git status                             # what changed?
git add <files> && git commit -m "fix: Describe it"
git push -u origin fix/my-fix          # first push of the branch
git push                               # later pushes
git log --oneline -5                   # recent commits
```

## Troubleshooting
- **"Updates were rejected"**: someone changed the branch. Run `git pull`, then push again.
- **commitlint failed**: fix the message with `git commit --amend -m "feat: Better message"`
  (last commit only), then `git push --force-with-lease`. Only do this on your own feature branch.
- **My PR says "out of date"**: click **Update branch** on the PR page, or run
  `git checkout dev && git pull && git checkout - && git merge dev`.
- **I committed to `dev` by mistake**: don't push. Ask for help; it is easy to fix before the push.

## Words you will see
- **Pull request (PR)**: a request to merge one branch into another, with reviews and checks.
- **CI**: the automatic checks on every PR (`.github/workflows/ci.yml`).
- **Preview**: the throwaway copy of the app created for a dev merge.
- **Squash merge**: turn all of a PR's commits into one.
- **Production (prod)**: the real service other teams call.
