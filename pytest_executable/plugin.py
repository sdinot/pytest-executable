# Copyright 2020, CS Systemes d'Information, http://www.c-s.fr
#
# This file is part of pytest-executable
#     https://www.github.com/CS-SI/pytest-executable
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Entry point into the pytest executable plugin."""

import logging
import sys
from functools import cmp_to_key
from pathlib import Path
from typing import Dict, List, Optional

import _pytest
import py
import pytest
from _pytest.config import Config
from _pytest.terminal import TerminalReporter

from . import report, test_case_yaml
from .file_tools import create_output_directory, find_references, get_mirror_path
from .script_runner import ScriptRunner, get_final_script
from .settings import Settings

LOGGER = logging.getLogger(__name__)

# files to be ignored when creating the output directories symlinks
OUTPUT_IGNORED_FILES = ("__pycache__", "conftest.py", "test_case.yaml")

EXE_RUNNER_NAME = "run_executable"

# file with the test default settings
SETTINGS_PATH = Path(__file__).parent / "test_case.yaml"

# caches the test case directory path to marks to propagate them to all the
# test modules of a test case
_marks_cache: Dict[str, List[str]] = {}


def pytest_addoption(parser):
    """CLI options for the plugin."""
    group = parser.getgroup("executable", "executable testing")
    group.addoption(
        "--runner",
        metavar="PATH",
        help="use the shell script at PATH to run an executable, if omitted then "
        "the executable is not run but the other test processing will be",
    )
    group.addoption(
        "--output-root",
        default="tests-output",
        metavar="PATH",
        help="use PATH as the root directory of the tests output, default: %(default)s",
    )
    group.addoption(
        "--overwrite-output",
        action="store_true",
        help="overwrite existing files in the tests output directories",
    )
    group.addoption(
        "--clean-output",
        action="store_true",
        help="clean the tests output directories before executing the tests",
    )
    group.addoption(
        "--regression-root",
        metavar="PATH",
        help="use PATH as the root directory with the references for the "
        "regression testing, if omitted then the tests using the regression_path "
        "fixture will be skipped",
    )
    group.addoption(
        "--default-settings",
        default=SETTINGS_PATH,
        metavar="PATH",
        help="use the yaml file at PATH for the global default test settings "
        "instead of the built-in one",
    )
    group.addoption(
        "--equal-nan",
        action="store_true",
        help="consider nan values as equal when doing comparison with the "
        "references for the built-in regression testing",
    )
    group.addoption(
        "--report-generator",
        metavar="PATH",
        help="use the script at PATH to generate a test report",
    )

    # change default traceback settings to get only the message without the
    # traceback
    term_rep_options = parser.getgroup("terminal reporting").options
    tb_option = next(
        option for option in term_rep_options if option.names() == ["--tb"]
    )
    tb_option.default = "line"


def pytest_sessionstart(session):
    """Check the cli arguments."""
    getoption = session.config.getoption
    if getoption("clean_output") and getoption("overwrite_output"):
        msg = "options --clean-output and --overwrite-output are not compatible"
        raise pytest.UsageError(msg)


def _get_parent_path(fspath: py.path.local) -> Path:
    """Return the resolved path to a parent directory.

    Args:
        fspath: Path object from pytest.

    Returns:
        Resolved path to the parent directory of the given pat.
    """
    return Path(fspath).parent.resolve(True)


@pytest.fixture(scope="module")
def equal_nan(request):
    """Fixture to whether consider nan as equal when comparing fields."""
    return request.config.getoption("equal_nan")


@pytest.fixture(scope="module")
def create_output_tree(request):
    """Fixture to create and return the path to the output directory tree."""
    getoption = request.config.getoption
    output_root = Path(getoption("output_root"))
    parent_path = _get_parent_path(request.node.fspath)
    output_path = get_mirror_path(parent_path, output_root.resolve())

    try:
        create_output_directory(
            parent_path,
            output_path,
            not getoption("overwrite_output"),
            getoption("clean_output"),
            OUTPUT_IGNORED_FILES,
        )
    except FileExistsError:
        msg = (
            f'the output directory "{output_path}" already exists: either '
            "remove it manually or use the --clean-output option to remove "
            "it or use the --overwrite-output to overwrite it"
        )
        raise FileExistsError(msg)


@pytest.fixture(scope="module")
def output_path(request):
    """Fixture to return the path to the output directory."""
    output_root = Path(request.config.getoption("output_root")).resolve(True)
    return get_mirror_path(_get_parent_path(request.node.fspath), output_root)


def _get_settings(config: _pytest.config.Config, path: Path) -> Settings:
    """Return the settings from global and local test_case.yaml.

    Args:
        config: Config from pytest.
        path: Path to a test case directory.

    Returns:
        The settings from the test case yaml.
    """
    return Settings.from_local_file(
        Path(config.getoption("default_settings")),
        _get_parent_path(path) / SETTINGS_PATH.name,
    )


@pytest.fixture(scope="module")
def tolerances(request):
    """Fixture that provides the settings from global and local test_case.yaml."""
    return _get_settings(request.config, request.node.fspath).tolerances


@pytest.fixture(scope="module")
def runner(request, create_output_tree, output_path):
    """Fixture to run the executable with a runner script.

    This fixture will create a :file:`run_executable.sh` script in the test case
    output directory from the script passed to the pytest command line with the
    option :option:`--runner`. The placeholders {nproc} and {output_path} are
    replaced with their actual values in the written script. The runner object
    created by the fixture can be executed with the :py:meth:`run` method
    which will return the return code of the script execution.

    Returns:
        ScriptRunner object.
    """
    runner_path = request.config.getoption("runner")
    if runner_path is None:
        pytest.skip("no runner provided to --runner")

    # check path
    runner_path = Path(runner_path).resolve(True)

    nproc = _get_settings(request.config, request.node.fspath).nproc

    variables = dict(output_path=output_path, nproc=nproc)
    script = get_final_script(runner_path, variables)
    return ScriptRunner(EXE_RUNNER_NAME, script, output_path)


def _get_regression_path(
    config: _pytest.config.Config, fspath: py.path.local
) -> Optional[Path]:
    """Return the path to the reference directory of a test case.

    None is returned if --regression-root is not passed to the CLI.

    Args:
        config: Config from pytest.
        fspath: Path to a test case directory.

    Returns:
        The path to the reference directory of the test case or None.
    """
    regression_path = config.getoption("regression_root")
    if regression_path is None:
        return None
    return get_mirror_path(
        _get_parent_path(fspath), Path(regression_path).resolve(True)
    )


@pytest.fixture(scope="module")
def regression_path(request):
    """Fixture to return the path of a test case under the references tree."""
    regression_path = _get_regression_path(request.config, request.node.fspath)
    if regression_path is None:
        pytest.skip("no tests references root directory provided to --regression-root")
    return regression_path


def pytest_generate_tests(metafunc):
    """Create the regression_file_path parametrized fixture.

    Used for accessing the references files.

    If --regression-root is not set then no reference files will be provided.
    """
    if "regression_file_path" not in metafunc.fixturenames:
        return

    # result absolute and relative file paths to be provided by the fixture parameter
    # empty means skip the test function that use the fixture
    file_paths = []

    regression_path = _get_regression_path(metafunc.config, metafunc.definition.fspath)

    if regression_path is not None:
        settings_path = metafunc.definition.fspath
        settings = _get_settings(metafunc.config, settings_path)

        if settings.references:
            file_paths = find_references(regression_path, settings.references)

    metafunc.parametrize(
        "regression_file_path",
        file_paths,
        scope="function",
        ids=list(map(str, [f.relative for f in file_paths])),
    )


def pytest_collect_file(parent, path):
    """Collect test cases defined with a yaml file."""
    if path.basename == SETTINGS_PATH.name:
        return TestCaseYamlModule(path, parent)


def pytest_configure(config):
    """Register the possible markers and change default error display.

    Display only the last error line without the traceback.
    """
    config.addinivalue_line(
        "markers", 'slow: marks tests as slow (deselect with -m "not slow")'
    )

    # show only the last line with the error message when displaying a
    # traceback
    if config.getoption("tbstyle") == "auto":
        config.option.tbstyle = "line"


class TestCaseYamlModule(pytest.Module):
    """Collector for tests defined with a yaml file."""

    def _getobj(self):
        """Override the base class method.

        To swap the yaml file with the test_case_yaml.py module.
        """
        # prevent python from using the module cache, otherwise the module
        # object will be the same for all the tests
        del sys.modules[test_case_yaml.__name__]
        # backup the attribute before temporary override of it
        fspath = self.fspath
        self.fspath = py.path.local(test_case_yaml.__file__)
        module = self._importtestmodule()
        # restore the backuped up attribute
        self.fspath = fspath
        # set the test case marks from settings.yaml
        settings = _get_settings(self.config, fspath)
        # store the marks for settings them later
        if settings.marks:
            _marks_cache[fspath.dirname] = settings.marks
        return module


def pytest_exception_interact(node, call, report):
    """Change exception display to only show the test path and error message.

    Avoid displaying the test file path and the Exception type.
    """
    excinfo = call.excinfo
    if excinfo.typename == "CollectError" and str(excinfo.value).startswith(
        "import file mismatch:\n"
    ):
        # handle when a custom test script is used in more than one test case with
        # the same name
        dirname = node.fspath.dirname
        filename = node.fspath.basename
        report.longrepr = (
            f"{dirname}\nshall have a __init__.py because {filename} "
            "exists in other directories"
        )
    else:
        report.longrepr.reprcrash = f"{report.nodeid}: {excinfo.value}"


def pytest_collection_modifyitems(
    session: _pytest.main.Session,
    config: _pytest.config.Config,
    items: List[_pytest.nodes.Item],
) -> None:
    """Change the tests execution order.

    Such that:
    - the tests in parent directories are executed after the tests in children
      directories
    - in a test case directory, the yaml defined tests are executed before the
      others (to handle the output directory creation).
    """
    items.sort(key=cmp_to_key(_sort_yaml_first))
    items.sort(key=cmp_to_key(_sort_parent_last))
    _set_marks(items)


def _sort_yaml_first(item_1: _pytest.nodes.Item, item_2: _pytest.nodes.Item) -> int:
    """Sort yaml item first if in the same directory."""
    path_1 = Path(item_1.fspath)
    path_2 = Path(item_2.fspath)
    if path_1.parent == path_2.parent and (path_1.suffix, path_2.suffix) == (
        ".yaml",
        ".py",
    ):
        return -1
    return 1


def _sort_parent_last(item_1: _pytest.nodes.Item, item_2: _pytest.nodes.Item) -> int:
    """Sort item in parent directory last."""
    dir_1 = Path(item_1.fspath).parent
    dir_2 = Path(item_2.fspath).parent
    if dir_2 in dir_1.parents:
        return -1
    return 1


def _set_marks(items: List[_pytest.nodes.Item]) -> None:
    """Set the marks to the test functions under a test case."""
    for dirname, marks in _marks_cache.items():
        for item in items:
            if item.fspath.dirname != dirname:
                continue
            for mark in marks:
                item.add_marker(mark)


def pytest_terminal_summary(
    terminalreporter: TerminalReporter, exitstatus: int, config: Config
) -> None:
    """Create the custom report.

    In the directory that contains the report generator, the report database is
    created and the report generator is called.
    """
    # path to the report generator
    reporter_path = config.getoption("report_generator")
    if reporter_path is None:
        return

    if not terminalreporter.stats:
        # no test have been run thus no report to create or update
        return

    output_root = Path(config.getoption("output_root"))

    terminalreporter.write_sep("=", "starting report generation")

    try:
        report.generate(reporter_path, output_root, terminalreporter)
    except Exception as e:
        terminalreporter.write_line(str(e), red=True)
        terminalreporter.write_sep("=", "report generation failed", red=True)
    else:
        terminalreporter.write_sep("=", "report generation done")
