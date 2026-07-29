# P8-002: Release Certification

- **Status:** PLANNED
- **Objective:** Produce a reproducible, installable MVP release with explicit limitations.
- **Requirements:** NFR-001, NFR-005, NFR-006, OPS-004, OPS-007, OPS-008.
- **Dependencies:** P8-001.
- **In scope:** version/changelog, package build/install smoke test, supported-platform acceptance,
  migration compatibility, security/release review, release notes and checklist.
- **Out of scope:** publishing/tagging/pushing without explicit authorization.
- **Steps:** freeze scope; run acceptance/live evidence; adversarial diff/security review; build wheels/sdist;
  install in clean env; verify hashes/content; document limitations; request publish authorization.
- **Security constraints:** no secrets/artifacts with transcripts; provenance/hashes retained; no auto-publish.
- **Acceptance criteria:** all release gates pass; artifact reproducible/installable; no critical/high finding;
  semantic version and compatibility/limitations are clear.
- **Verification:** canonical gates plus `uv build`, clean-environment install/smoke, artifact inspection/hash.
- **Completion evidence:** Not started.
- **Risks:** packaging metadata/license decision and late cross-platform defects.
- **Recommended model/effort:** GPT-5.6 Sol, max for certification.
- **Next package:** post-MVP roadmap selected from measured evidence.
