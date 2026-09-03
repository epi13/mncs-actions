# Security

Please do not disclose security-sensitive issues in a public issue.

Use GitHub's private vulnerability reporting for this repository when available. If that is unavailable, contact the repository owner privately with:

- the affected action or workflow;
- the minimum reproduction;
- the security impact;
- whether the issue affects GitHub-hosted or self-hosted runners.

## Self-hosted runners

Never allow untrusted pull-request code to execute on a privileged self-hosted runner. A runner can expose the filesystem, environment, network, and credentials made available to its host.

In particular:

- keep public validation on isolated, disposable runners;
- separate trusted experiment dispatch from ordinary pull-request verification;
- use least-privilege workflow permissions;
- do not place long-lived credentials on experiment runners;
- treat artifact contents as untrusted input;
- record the runner boundary in provenance when hardware-dependent execution is introduced.

The verification action is evidence plumbing. It does not make an unsafe runner safe.
