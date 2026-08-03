# Octane Houdini Open Tools

Octane Houdini Open Tools is a supplemental, community-driven toolset for
[OctaneRender](https://render.otoy.com/) in SideFX Houdini.

This is an independent community project. It is not an official OTOY
repository and is not maintained or endorsed by OTOY. References to OTOY,
OctaneRender, or the official Octane toolset do not imply affiliation.

The project provides a home for practical workflows, importers, utilities, and
experiments that improve the Octane experience in Houdini. Some tools may
eventually be suitable for inclusion in Octane's core Houdini toolset; others
may remain here because they are specialized, experimental, or too narrowly
focused for the official distribution. Both kinds of contribution are welcome.

## Project Goals

- Extend and streamline Octane workflows in Houdini.
- Make useful tools available to the wider community.
- Encourage shared solutions instead of duplicated one-off scripts.
- Provide a proving ground for ideas that may later inform or become part of
  Octane's core toolset.
- Keep specialized tools available even when they are not a fit for the
  official toolset.

## Included Tools

The repository currently includes shared material and settings APIs, import
utilities, shelf tools, and integrations for services such as Cargo and
Megascans.

See the [documentation](docs/README.md) for the available tools and APIs.

## Installation

1. Clone or download this repository to a permanent location.
2. Open `OctaneHoudiniOpenTools.json` and replace
   `PATH/TO/OctaneHoudiniOpenTools` with the absolute path to the repository.
3. Copy the edited JSON file into your Houdini user packages directory:
   `$HOUDINI_USER_PREF_DIR/packages`.
4. Restart Houdini.

OctaneRender for Houdini must already be installed and configured. Individual
tools may have additional requirements documented under [`docs/`](docs/README.md).

## Contributing

Pull requests are welcome, whether they introduce a broadly useful workflow,
improve an existing tool, fix a bug, add documentation, or explore a more
specialized idea.

Before opening a pull request:

- Test the change thoroughly in the supported Houdini and Octane versions.
- Describe what changed, why it is useful, and how it was tested.
- Include clear reproduction steps for bug fixes.
- Keep user-specific paths, credentials, generated files, and private assets
  out of the repository.
- Update documentation when behavior or setup changes.
- Prefer shared utilities over duplicating common node-creation or settings
  logic.

Contributors should expect submissions to be reviewed and vetted before they
are merged. A contribution may also be evaluated for adaptation or inclusion
in Octane's official core toolset. Submitting a tool does not guarantee that it
will be merged here or included in an official release.

## AI-Assisted Development

AI tools have been used in the creation of this project, and AI-assisted
contributions are welcome.

The contributor remains responsible for every submitted change. AI-generated
or AI-assisted code must be understood, reviewed, and thoroughly tested before
a pull request is opened. Please verify correctness, compatibility, security,
licensing, and maintainability just as you would for code written without AI
assistance. Mention meaningful AI assistance in the pull request when it helps
reviewers understand how the change was produced or validated.

## License and Upstream Use

Unless otherwise noted, this project is licensed under the
[MIT License](LICENSE).

By submitting a contribution, you agree to license it under the MIT License.
The required copyright and license notices must be preserved.

The MIT License permits anyone, including OTOY, to review, adapt, and
incorporate the tools into other software subject to its terms. Possible
upstream adoption is part of the project's purpose, but it is not a promise
that any contribution will become an official Octane feature. Third-party
components retain their respective licenses where separately identified.
