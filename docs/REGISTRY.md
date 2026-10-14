# Sharing rules: a self-hosted registry

A rule registry is a git repository of **rule cards**: one YAML file per rule in `rules/`, each
with its statement, its reason and the check that says when it breaks
([RULE-CHECKS.md](RULE-CHECKS.md)). There is no service: hosting a registry is hosting a git
repository, on GitHub, GitLab, a company server or a directory.

```
rules-registry/
├── README.md
└── rules/
    ├── money.decimal.yaml
    └── deps.reviewed.yaml
```

```yaml
# rules/deps.reviewed.yaml
id: deps.reviewed
statement: A dependency shall be added only in a change a person reviews for the dependency.
rationale: Agents add packages to finish a task; each one is supply-chain risk ...
type: process
check: {no_new_dependencies: true}
tags: [python, node, dependencies, agents]
```

## Commands

```bash
specdeck rules init ~/rules-registry                       # a new registry: git repo + rules/
specdeck rules publish money.decimal --registry ~/rules-registry   # from this project's requirements.yaml; commits
git -C ~/rules-registry push                                # sharing is pushing

export SPECDECK_REGISTRY=https://github.com/acme/rules-registry.git   # or a directory
specdeck rules search money                                 # find rules, with how each is checked
specdeck rules show money.decimal                           # the card
specdeck rules add money.decimal                            # into requirements.yaml, with its check and source
```

- A directory is read in place. A git URL is cloned into `$SPECDECK_HOME/registries` (default
  `~/.specdeck`) on first use and pulled on each later use, so new rules arrive on their own.
- `add` appends the rule to `requirements.yaml`, keeping the file's comments and layout, and records
  `source: <registry>@<commit>`. From then on the rule is the project's: checked on every run,
  shown in AGENTS.md / CLAUDE.md, counted by `specdeck breaks`. Edit its paths to fit the project.
- `publish` writes to a checkout of the registry and commits; pushing is the publisher's step, so
  review can happen the way the host does it (a pull request).

## What can be shared

Only rules that say why and how they are checked: a card needs a rationale and a well-formed
check, and never a `command` check. Every shared check is data, so adding a rule from a registry
never runs someone else's code. Card text does end up in the agents' instructions, so read a rule
before committing it.

## Try it

`examples/registry/make-registry.sh DIR` builds a registry from seven generic, checkable rules
(`examples/registry/seed-rules.yaml`): Decimal money, timezone-aware time, reviewed
dependencies, no print in library code, no new test skips, no key files, network only through the
gateway.

## Not yet

- Statistics travelling with a rule (how often it breaks where it is used); the break log
  (`specdeck breaks`) is the local half of that.
- Parameters for project-specific names (which module is the gateway); today the adopter edits the
  paths after adding.
- Telling an adopter when a rule overlaps one the project already has.
