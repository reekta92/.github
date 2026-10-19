# Shared GitHub workflows

`rust-release.yml` releases a root Rust package. Callers select the release kind and pin the reusable workflow to a reviewed commit SHA. Release inputs belong to the caller; publishing credentials belong to the repository being released.

| Repository | Release kind | Outputs |
| --- | --- | --- |
| pinstar | `crate` | crates.io; all Cargo features checked |
| graf | `crate` | crates.io (`graf-rs`) |
| draw-rs | `crate` | crates.io |
| clin-rs | `application` | crates.io, Linux/Windows/macOS archives, .deb/.rpm, AppImage, DMG |
| sicth | `application` | Same application formats, including Intel macOS; Nix checked |

Crate callers expose only version controls, the target branch, dry runs, and the quality-gate escape hatch. Application callers also expose their relevant packaging options. Automatic AUR publishing is intentionally absent. Application releases can still update README and PKGBUILD references for downstream packagers; library releases do not update PKGBUILD files.

## Release guarantees

1. Resolve the target branch once, calculate a strictly newer semver, update only the root package version, and create a local release commit. Dependency changes during the version update fail the run.
2. Run formatting, Clippy, and tests on that commit. When crates.io publishing is enabled, `cargo publish --dry-run --locked` remains mandatory even when `skip_gates` is selected. This checks the packaged crate against registry dependencies rather than relying on Git dependencies or workspace patches.
3. Pass that exact commit to platform builds in a Git bundle. Native Linux jobs reuse their built binary for archives, .deb/.rpm, and AppImage. Dry runs build the candidate version without pushing a branch, creating a tag, or publishing anything.
4. After successful checks and builds, request approval through the `release-approval` environment (or `RELEASE_APPROVAL_ENVIRONMENT`). Configure required reviewers in repository settings if approval is required. Dry runs never request publishing approval.
5. Require selected publishing credentials, verify collected checksums, and push the release commit and tag atomically. A changed target branch aborts the run; it is never rebased into an untested release. Tags are never force-moved.
6. Publish crates.io first, then application GitHub releases. Published versions are never deleted on later failures. A retry of the failed publishing job accepts only the same release commit and, for existing crates.io versions, the same package checksum. Existing public GitHub releases are not overwritten; incomplete drafts can resume uploading assets.
7. Application documentation updates use the current branch and a normal push. Concurrent edits can reject that push without rewriting history or rolling back a published release.

The default stable bump is `patch`. A stable version starts its next prerelease at the selected bumped version (for example, `1.2.3` becomes `1.2.4-rc.0`). Existing `rc.N` prereleases increment `N`, and disabling prerelease graduates to the stable base. An exact version overrides these controls and must be strictly newer, including when only build metadata differs. The actual version determines GitHub's prerelease flag.

## Credentials and repository setup

- Add `CARGO_REGISTRY_TOKEN` as a repository secret with permission to publish that repository's crate. Dry runs need no registry token. Missing credentials fail real publication rather than silently skipping it.
- `BOT_TOKEN` is optional; it is used only by publishing and documentation steps when branch protection requires a separate token. Otherwise `GITHUB_TOKEN` is used with `contents: write`. Preparations and builds use read permissions and do not persist checkout credentials.
- Optional discussion creation requires `BOT_TOKEN` with discussion permissions and an `Announcements` discussion category.
- Keep a committed `Cargo.lock`, a root `[package]` with an explicit version, and published dependencies supporting the features used by the packaged crate. Virtual workspaces are not supported.
- Application packaging requires `LICENSE`, `README.md`, and the selected Cargo packaging metadata. AppImage additionally requires `assets/<binary>.desktop` and `assets/<binary>.png`.

`clin-rs` currently uses Git dependencies. Publish compatible library versions before publishing the application on crates.io. In particular, the registry version of `pinstar` must expose `mermaid-preview`; a Git-only feature cannot be published through a downstream crate.

## Updating callers

Push the shared workflow commit first, then update every caller's `uses` reference to its full SHA. Callers can adopt that SHA before this repository's default branch changes. This avoids breaking older callers on `@main` while inputs are removed. Keep caller references pinned when later changing the shared workflow.

## Continuous integration

`ci.yml` checks formatting without committing changes or requesting write access. Callers pin it to the same shared workflow commit; check names remain unchanged.

## Validation

```sh
actionlint
python3 -m pip install PyYAML==6.0.3
python3 -m unittest discover -s .github/scripts -p 'test_*.py' -v
```

Tests execute the workflow's actual inline scripts against temporary repositories: semver ordering, invalid inputs, crate/application selection, candidate restoration, dependency lock preservation, concurrent branch changes, atomic push rejection, and retry behavior. The `Workflow Tests` workflow runs these checks for shared workflow changes.
