# Shared GitHub workflows

`rust-release.yml` releases a root Rust package. Callers select the release kind and pin the reusable workflow to a reviewed commit SHA. Release inputs belong to the caller; publishing credentials belong to the repository being released.

| Repository | Release kind | Outputs |
| --- | --- | --- |
| pinstar | `crate` | crates.io, GitHub release, Linux CLI archives; all Cargo features checked and built |
| graf | `crate` | crates.io (`graf-rs`), GitHub release, Linux CLI archives |
| draw-rs | `crate` | crates.io, GitHub release, Linux CLI archives |
| clin-rs | `application` | crates.io, Linux/Windows/macOS archives, .deb/.rpm, AppImage, DMG |
| sicth | `application` | Same application formats, including Intel macOS; Nix checked |

Crate callers expose version controls, the target branch, dry runs, and the quality-gate escape hatch. They select `BIN_NAME`, `PKG_PREFIX` and `build_targets: linux-only` to build Linux x86_64 and ARM64 CLI archives. Application callers also expose their relevant desktop packaging options. Automatic AUR publishing is intentionally absent. Application releases can still update README and PKGBUILD references for downstream packagers.

Every crate release creates a GitHub release after crates.io publication, with versioned CLI installation and Rust dependency instructions. It attaches the published `.crate` source archive and `SHA256SUMS`. Binary callers also attach `<prefix>-<version>-<target>.tar.xz` and `.tar.gz` (ZIP on Windows). Linux GNU binaries require glibc 2.35 or newer. Binary archives contain the executable at the root, `LICENSE`, and `README.md`; AUR maintainers can use the Linux archives and their checksums directly in architecture-specific PKGBUILD sources. Source packages can use the `.crate` archive or GitHub's tag source archive. Application archive names remain unchanged.

## Release guarantees

1. Resolve the target branch once, calculate a strictly newer semver, update only the root package version, and create a local release commit. Dependency changes during the version update fail the run.
2. Run formatting, Clippy, and tests on that commit. When crates.io publishing is enabled, `cargo publish --dry-run --locked` remains mandatory even when `skip_gates` is selected. Callers requesting `all_features` also verify the all-feature package, preserving default-feature verification. This checks the packaged crate against registry dependencies rather than relying on Git dependencies or workspace patches. Repositories with `deny.toml` also run cargo-deny against the extracted package in an isolated directory, using the repository's policy and every feature; `skip_gates` cannot skip this check.
3. Pass that exact commit to platform builds in a Git bundle. Native Linux jobs reuse their built binary for archives, .deb/.rpm, and AppImage. Dry runs build the candidate version without pushing a branch, creating a tag, or publishing anything.
4. After successful checks and builds, request approval through the `release-approval` environment (or `RELEASE_APPROVAL_ENVIRONMENT`). Configure required reviewers in repository settings if approval is required. Dry runs never request publishing approval.
5. Require selected publishing credentials, verify collected checksums, and push the release commit and tag atomically. A changed target branch aborts the run; it is never rebased into an untested release. Tags are never force-moved.
6. Publish crates.io first, then the GitHub release with verified assets. Published versions are never deleted on later failures. A retry of the failed publishing job accepts only the same release commit and, for existing crates.io versions, the same package checksum. Existing public GitHub releases are not overwritten; incomplete drafts can resume uploading assets. A failed upload leaves a draft and fails the run.
7. Application documentation updates use the current branch and a normal push. Concurrent edits can reject that push without rewriting history or rolling back a published release.

The default stable bump is `patch`. A stable version starts its next prerelease at the selected bumped version (for example, `1.2.3` becomes `1.2.4-rc.0`). Existing `rc.N` prereleases increment `N`, and disabling prerelease graduates to the stable base. An exact version overrides these controls and must be strictly newer, including when only build metadata differs. The actual version determines GitHub's prerelease flag.

## Credentials and repository setup

- Add `CARGO_REGISTRY_TOKEN` as a repository secret with permission to publish that repository's crate. Dry runs need no registry token. Missing credentials fail real publication rather than silently skipping it.
- `BOT_TOKEN` is optional; it is used only by publishing and documentation steps when branch protection requires a separate token. Otherwise `GITHUB_TOKEN` is used with `contents: write`. Preparations and builds use read permissions and do not persist checkout credentials.
- Optional discussion creation requires `BOT_TOKEN` with discussion permissions and an `Announcements` discussion category.
- Keep a committed `Cargo.lock`, a root `[package]` with an explicit version, and published dependencies supporting the features used by the packaged crate. Virtual workspaces are not supported.
- Binary packaging requires `LICENSE`, `README.md`, a valid root `BIN_NAME` target and a safe `PKG_PREFIX`. Application desktop packages also require the selected Cargo packaging metadata. AppImage additionally requires `assets/<binary>.desktop` and `assets/<binary>.png`.

Publish compatible library versions before publishing the application on crates.io.
Clin's supported engine versions belong in its manifest and exact selections in
its lockfile. The `mermaid-preview` feature uses published `merman-core-clin`
compatibility code so its maintained YAML backend reaches registry consumers.

## Updating callers

Merge and verify the shared workflow change on main first, then update callers'
`uses` references to the full tested SHA. Retarget outstanding stacked PRs when
their parent merges. Keep callers pinned while later changing the shared workflow;
test changes to the caller contract before adopting the new reference.

## Continuous integration

`ci.yml` uses read-only jobs:

- **Check:** formatting first, then Clippy and tests (including doctests) on stable Rust. These checks share one checkout, toolchain setup, and cache restore; Cargo can still compile separately for checking and linking. A separate stable `cargo check` is unnecessary because Clippy already checks all targets.
- **MSRV:** read the root package's explicit `rust-version`, install that exact minimum (for example, `1.88` becomes `1.88.0`), and check all targets. This replaces both the previous shared Rust 1.90 constant and draw-rs's extra MSRV job.
- **Deny:** check advisories, licenses, bans, and sources against the committed lockfile, including every feature. Repository dependency policies remain authoritative; CI does not suppress failures.
- **Test (windows-latest)** and **Test (macos-latest):** run the native test suite, including doctests, on supported operating systems.
- **Package:** compile the registry package with default and requested optional features, then apply `deny.toml` to the extracted archive. This catches missing packaged files and dependency patches that do not reach registry users.

The `all_features` input enables optional integrations for tests, Clippy, and MSRV. The pinstar caller enables it for Mermaid; other current callers use their default features. pinstar declares Rust 1.90 because its locked image dependency graph already requires that compiler; graf declares Rust 1.88, and the other applications/libraries declare Rust 1.90.

Rust cache keys include the selected feature mode and compiler version. Only successful default-branch push jobs save Rust caches. Pull requests restore the default-branch cache without uploading duplicate entries. Formatting has no compilation cache to restore. Concurrency cancels obsolete runs; every job has a time limit and checkouts do not retain credentials.

Callers pin CI independently from the release workflow. Require `ci / Check`,
`ci / MSRV`, `ci / Deny`, `ci / Package`, `ci / Test (windows-latest)`, and
`ci / Test (macos-latest)` in branch rules. Libraries also require
`clin-compat / Clin adapter`; Clin requires `development-tools`. The shared
repository requires `workflow-tests`, which runs on every PR so documentation
changes can satisfy its branch rules.

Native tests cover automated filesystem, recovery, input, and rendering contracts.
Before an application release, also perform the interactive checklist in Clin's
`docs/RELEASE_CHECKLIST.md`; hosted runners cannot prove a real terminal's behavior.

## Protected releases

Every release caller must grant `contents: write`, `actions: write` and
`statuses: write`, and its
`ci.yml` must accept `workflow_dispatch`. After preparing and building a real
release, the workflow pushes its exact candidate to a temporary
`release-candidate/<run>-<attempt>` branch, dispatches CI, and waits for that
commit's run to succeed before requesting release approval and publishing.
The explicit dispatch works with `GITHUB_TOKEN`; no branch-rule bypass or bot
token is needed. Dry runs create no branches or publishing mutations.

GitHub does not count dispatched check suites toward a direct protected-branch
push. After the exact candidate run succeeds, the workflow records each successful
job as a commit status, linking to that job. The same required check names and
GitHub Actions identity still apply; failed or skipped jobs receive no success
status, and status publication errors abort the release. Documentation commits
use the same process.

Post-release documentation commits pass the same CI on `release-docs/` branches
before their ordinary push to the target. Successful publication removes its
temporary branches. Failed candidates remain available for investigation and can
be removed after confirming that the release run is no longer active.

Keep strict required status checks, deletion protection, and force-push
protection on main, with no routine bypass actors. Administrators can temporarily
change a rule to repair broken automation, recording and reverting the exception.
New workflow changes must reach this repository's main
before callers adopt them; retarget outstanding stacked PRs when their parent merges.

## Downstream Clin contracts

Library CI can call `clin-compat.yml` with a full `clin_ref` commit SHA containing
Clin's tracked `scripts/check_upstream.py`. The workflow checks out the caller's
candidate and that exact Clin consumer with read permissions and no persisted
credentials. The consumer script patches the actual Git dependency source in a
temporary copy, verifies candidate selection, and runs the matching existing
Graf, Canvas, or Draw adapter tests. Original consumer pins remain unchanged.
Callers keep their ordinary standalone CI and pin this reusable workflow to a
reviewed full SHA independently. Paired changes link the library PR, compatible
Clin commit, library release, and subsequent Clin pin-update PR.

## Packaged dependency policy

Root `[patch]` entries do not propagate to published packages. A green source
checkout therefore does not establish a green registry dependency graph.
The extracted-package policy gate deliberately rejects advisories and
license/source violations even if a source-only compatibility patch resolves
them locally. Pinstar's published compatibility dependency carries its maintained
YAML backend into consumers. Keep both the source and packaged graphs passing;
an advisory exception is not a replacement for fixing a release dependency.

## Validation

```sh
actionlint
python3 -m pip install PyYAML==6.0.3
cargo install cargo-deny --locked
python3 -m unittest discover -s .github/scripts -p 'test_*.py' -v
```

Tests execute the workflow's actual inline scripts against temporary repositories: semver ordering, invalid inputs, crate/application selection, candidate restoration, dependency lock preservation, concurrent branch changes, atomic push rejection, and retry behavior. The `Workflow Tests` workflow runs these checks for shared workflow changes.
