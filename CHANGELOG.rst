.. _`changelog`:

Changelog
=========

All notable changes to this project will be documented here.

The format is based on `Keep a Changelog <https://keepachangelog.com/en/1.0.0/>`_,
and this project adheres to `Semantic Versioning <https://semver.org/spec/v2.0.0.html>`_.

0.3.0 - Unreleased
------------------

Added
~~~~~
- How to use skip and xfail marks in the docs.
- How to use a proxy with anaconda in the docs.
- Better error message when :option:`--runner` do not get a script.

Changed
~~~~~~~
- Placeholder in the runner script are compliant with bash (use {{}} instead of {}).
- Report generation is done for all the tests at once and only requires a report generator script.

Fixed
~~~~~
- #8393: check that :option:`--clean-output` and :option:`--overwrite-output` are not used both.
- Output directory creation no longer fails when the input directory tree has one level.

Removed
~~~~~~~
- Useless :option:`--nproc` command line argument, because this can be done with a custom default :file:`test_case.yaml` passed to the command line argument :option:`--default-settings`.

0.2.1 - 2020-01-14
------------------

Fixed
~~~~~
- #7043: skip regression tests when reference files are missing, no longer raise error.
