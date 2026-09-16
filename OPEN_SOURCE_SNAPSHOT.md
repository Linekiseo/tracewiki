# Open-Source Snapshot Provenance

This repository started as a curated, single-root public snapshot of the TraceWiki V29 engineering candidate.

- Source commit: `7cc7dcdfcc8e51e92b1b1642dca6189cf4ca1f46`
- Source tree: `869d21b0cccadf472192c1e6d2f349479e4ba60c`
- Snapshot date: 2026-09-16
- Publication model: new public root commit; private/local development history was not published

The application source, tests, fixtures, build definitions, lock files, evaluation runs and product documentation were copied
from that tree. Public-only changes were limited to:

- adding the MIT license and contribution/security documentation;
- adding public repository metadata to the README and Python package metadata;
- adding ordinary public project CI while retaining strict G0 admission for explicit manual runs or configured authority;
- restoring the reviewed Code Golden historical fixture from its checked-in bundle to a CI-local fixture branch before
  backend tests; that branch is never published;
- replacing a machine-specific home-directory prefix in retained text records with `/Users/example`;
- excluding 8 unreferenced generated root images, 3 temporary design-comparison exports, 1 non-portable visual QA log,
  2 duplicate DOCX files and 2 duplicate ZIP bundles.

The twelve `docs/design/*.png` files intentionally kept outside source admission were not present in the source tree and were
not added to this public snapshot. Generated `web/index.html` is also not tracked.

This provenance statement describes source lineage; it is not a production-readiness or release-qualification claim.
