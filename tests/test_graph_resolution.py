"""Import parsing and module resolution."""

from __future__ import annotations

import pytest

from impact.graph import py_imports as PY
from impact.graph import ts_imports as TS


# ------------------------------------------------------------- python ----


def test_python_absolute_and_relative_imports():
    source = (
        "import os\n"
        "from posthog.models import Team\n"
        "from . import sibling\n"
        "from ..utils import helper\n"
    )
    imports, error = PY.parse_python_imports(source)
    assert error is None
    modules = [(i.module, i.level) for i in imports]
    assert ("os", 0) in modules
    assert ("posthog.models", 0) in modules
    assert (None, 1) in modules
    assert ("utils", 2) in modules


def test_python_type_checking_imports_are_labelled():
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from posthog.models import Team\n"
    )
    imports, _ = PY.parse_python_imports(source)
    team = [i for i in imports if i.module == "posthog.models"]
    assert team and team[0].is_type_only is True


def test_python_function_local_import_is_dynamic():
    source = "def f():\n    import json\n    return json\n"
    imports, _ = PY.parse_python_imports(source)
    assert imports[0].is_dynamic is True


def test_python_syntax_error_is_reported_not_swallowed():
    imports, error = PY.parse_python_imports("def broken(:\n")
    assert imports == []
    assert error and "SyntaxError" in error


def test_module_index_prefers_package_init():
    index = PY.build_module_index(["posthog/models/__init__.py", "posthog/models.py"])
    assert index["posthog.models"] == "posthog/models/__init__.py"


def test_relative_import_resolves_upward():
    index = PY.build_module_index(["a/b/c.py", "a/utils.py"])
    imp = PY.PyImport(module="utils", names=[], level=2, is_type_only=False,
                      is_dynamic=False, lineno=1)
    target, how = PY.resolve_python_import(imp, from_path="a/b/c.py", module_index=index)
    assert target == "a/utils.py"
    assert how == "exact"


def test_third_party_import_is_external_not_a_failure():
    index = PY.build_module_index(["posthog/x.py"])
    imp = PY.PyImport(module="django.db", names=[], level=0, is_type_only=False,
                      is_dynamic=False, lineno=1)
    target, how = PY.resolve_python_import(imp, from_path="posthog/x.py", module_index=index)
    assert target is None
    assert how == "external"


# --------------------------------------------------------- typescript ----


def test_ts_import_forms():
    source = """
    import React from 'react'
    import type { Foo } from './types'
    import { a, b } from '~/lib/utils'
    export { x } from './x'
    const y = require('./y')
    const z = await import('./z')
    """
    imports, _ = TS.parse_ts_imports(source)
    specs = {i.specifier for i in imports}
    assert specs == {"react", "./types", "~/lib/utils", "./x", "./y", "./z"}
    kinds = {i.specifier: i.kind for i in imports}
    assert kinds["./types"] == "type_import"
    assert kinds["./x"] == "reexport"
    assert kinds["./y"] == "require"
    assert kinds["./z"] == "dynamic"


def test_commented_out_imports_are_ignored():
    """A specifier inside a comment must not create an edge."""
    source = "// import { x } from './ghost'\n/* import y from './ghost2' */\nimport a from './real'\n"
    imports, _ = TS.parse_ts_imports(source)
    assert {i.specifier for i in imports} == {"./real"}


def test_dynamic_template_import_is_flagged_as_uncertain():
    imports, has_dynamic = TS.parse_ts_imports("const m = await import(`./locales/${l}`)")
    assert has_dynamic is True


def test_upward_relative_specifier_resolves():
    """pathlib does not collapse '..'; using it here silently broke every
    upward relative import until a coverage check caught it."""
    files = {"a/src/filter.ts", "a/__tests__/filter.test.ts"}
    resolver = TS.TsResolver(files, [TS.TsConfigScope(directory="", aliases={})])
    target, how = resolver.resolve("../src/filter", "a/__tests__/filter.test.ts")
    assert target == "a/src/filter.ts"
    assert how == "relative"


def test_relative_index_file_resolution():
    files = {"a/b/index.ts"}
    resolver = TS.TsResolver(files, [TS.TsConfigScope(directory="", aliases={})])
    target, how = resolver.resolve("./b", "a/main.ts")
    assert target == "a/b/index.ts"


def test_nearest_tsconfig_wins_over_root():
    """PostHog defines '~/*' differently per workspace; resolving everything
    against the root config mis-resolves thousands of edges."""
    files = {"frontend/src/utils.ts", "nodejs/src/utils.ts"}
    scopes = [
        TS.TsConfigScope(directory="", aliases={"~/*": ["./frontend/src/*"]}),
        TS.TsConfigScope(directory="nodejs", aliases={"~/*": ["./src/*"]}),
    ]
    resolver = TS.TsResolver(files, scopes)
    assert resolver.resolve("~/utils", "nodejs/src/main.ts")[0] == "nodejs/src/utils.ts"
    assert resolver.resolve("~/utils", "frontend/src/main.ts")[0] == "frontend/src/utils.ts"


def test_node_modules_alias_is_external_not_unresolved():
    files = {"frontend/src/a.ts"}
    scopes = [TS.TsConfigScope(directory="",
                               aliases={"@posthog/icons": ["./frontend/node_modules/@posthog/icons"]})]
    resolver = TS.TsResolver(files, scopes)
    assert resolver.resolve("@posthog/icons", "frontend/src/a.ts") == (None, "external_alias")


def test_bare_package_is_external():
    resolver = TS.TsResolver(set(), [TS.TsConfigScope(directory="", aliases={})])
    assert resolver.resolve("react", "a.ts") == (None, "external")


def test_jsonc_tsconfig_with_comments_and_trailing_commas_parses():
    source = """
    {
        // a comment containing "quotes" and a url https://x.test
        "compilerOptions": {
            /* block */
            "paths": {
                "lib/*": ["./frontend/src/lib/*"],
            },
        },
    }
    """
    paths, error = TS.load_tsconfig_paths(source)
    assert error is None
    assert paths == {"lib/*": ["./frontend/src/lib/*"]}


def test_unparseable_tsconfig_reports_an_error():
    paths, error = TS.load_tsconfig_paths("{ this is not json")
    assert paths == {}
    assert error and "unparseable" in error
