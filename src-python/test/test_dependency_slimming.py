"""軽量化 (A-1) で外した依存が戻ってこないことを確かめる。"""

import ast
import json
import os
import re
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SRC_PYTHON = os.path.join(_REPO_ROOT, "src-python")
_EXCLUDED_DIRS = {"test", "docs", "__pycache__", "weights"}


def _requirementNames() -> set[str]:
    names = set()
    with open(os.path.join(_REPO_ROOT, "requirements.txt"), encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-r"):
                continue
            name = re.split(r"[\s=<>@\[;]", line, maxsplit=1)[0]
            names.add(name.lower().replace("_", "-"))
    return names


def _importedTopLevelModules() -> set[str]:
    modules = set()
    for dirpath, dirnames, filenames in os.walk(_SRC_PYTHON):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            with open(os.path.join(dirpath, filename), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    modules.add(node.module.split(".")[0])
    return modules


class FrontendDependencyTests(unittest.TestCase):
    def test_unused_frontend_packages_are_not_declared(self) -> None:
        with open(os.path.join(_REPO_ROOT, "package.json"), encoding="utf-8") as f:
            package = json.load(f)
        declared = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))
        for name in ("@babel/standalone", "jszip", "semver"):
            with self.subTest(package=name):
                self.assertNotIn(name, declared)


class LangchainRemovedTests(unittest.TestCase):
    def test_langchain_is_not_required(self) -> None:
        for name in ("langchain-openai", "langchain-google-genai", "langchain-ollama"):
            with self.subTest(package=name):
                self.assertNotIn(name, _requirementNames())

    def test_langchain_is_not_imported(self) -> None:
        imported = _importedTopLevelModules()
        for module in ("langchain_openai", "langchain_google_genai", "langchain_ollama", "langchain_core"):
            with self.subTest(module=module):
                self.assertNotIn(module, imported)

    def test_openai_sdk_is_declared_explicitly(self) -> None:
        self.assertIn("openai", _requirementNames())


class TransformersRemovedTests(unittest.TestCase):
    def test_transformers_is_not_required_at_runtime(self) -> None:
        self.assertNotIn("transformers", _requirementNames())

    def test_transformers_is_not_imported_by_the_app(self) -> None:
        self.assertNotIn("transformers", _importedTopLevelModules())


if __name__ == "__main__":
    unittest.main()
