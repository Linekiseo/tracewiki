# Document Golden v1

This directory records the immutable release identity for the programmatic
Scientific Document Golden. The byte-exact publications and 50 frozen cases are
rebuilt by `build_document_fixture_v1()` and `build_document_golden_v1()`; their
content-addressed package, authority, recipe, ordered membership, slice counts,
and entity counts must match `release.json`.

The fixture is test evidence only. It does not read or write the service-owned
production database and it is not a production evaluation run.
