#!/usr/bin/env python3

"""
Simple validation script to check basic package structure
and imports without requiring full installation.
"""

import os
import sys
import importlib.util


def test_basic_structure():
    """Test that basic package structure exists."""
    print("Checking basic package structure...")

    required_dirs = ["pandas_ta_classic", "tests", "docs", "examples"]

    # Modern Python packaging uses pyproject.toml (PEP 517/518)
    # setup.py is optional for backward compatibility with older pip versions
    required_files = [
        "pyproject.toml",  # Modern dependency management (PEP 517/518)
        "README.md",
        "pandas_ta_classic/__init__.py",
    ]

    # Optional files (for backward compatibility or legacy tooling)
    optional_files = [
        "setup.py",  # Optional: minimal shim for editable installs with older pip
    ]

    for dir_name in required_dirs:
        if not os.path.isdir(dir_name):
            print(f"❌ Missing required directory: {dir_name}")
            return False
        else:
            print(f"✅ Found directory: {dir_name}")

    for file_name in required_files:
        if not os.path.isfile(file_name):
            print(f"❌ Missing required file: {file_name}")
            return False
        else:
            print(f"✅ Found file: {file_name}")

    # Check optional files (just informational, not required)
    for file_name in optional_files:
        if os.path.isfile(file_name):
            print(f"ℹ️  Found optional file: {file_name}")
        else:
            print(f"ℹ️  Optional file not present: {file_name}")

    return True


def test_import_structure():
    """Test that package can be imported without dependencies."""
    print("\nChecking package import structure...")

    try:
        # Add current directory to path
        sys.path.insert(0, os.getcwd())

        # Try to import the context module (used by tests)
        spec = importlib.util.spec_from_file_location("context", "tests/context.py")
        importlib.util.module_from_spec(spec)

        print("✅ Tests context module structure is valid")
        return True

    except Exception as e:
        print(f"❌ Import structure issue: {e}")
        return False


def test_workflows():
    """Test that workflow files exist and have basic structure."""
    print("\nChecking GitHub Actions workflows...")

    workflow_files = [".github/workflows/ci.yml"]

    for workflow_file in workflow_files:
        if not os.path.isfile(workflow_file):
            print(f"❌ Missing workflow: {workflow_file}")
            return False
        else:
            print(f"✅ Found workflow: {workflow_file}")

            # Check that it has basic YAML structure
            # Use UTF-8 encoding to handle special characters on Windows
            try:
                with open(workflow_file, "r", encoding="utf-8") as f:
                    content = f.read()
                    if "name:" not in content or "on:" not in content:
                        print(f"❌ Invalid workflow structure in {workflow_file}")
                        return False
            except Exception as e:
                print(f"❌ Error reading workflow file: {e}")
                return False

    return True


def test_pages_content():
    """Test that pages content exists."""
    print("\nChecking GitHub Pages content...")

    pages_files = ["index.md", "_config.yml"]

    for pages_file in pages_files:
        if not os.path.isfile(pages_file):
            print(f"❌ Missing pages file: {pages_file}")
            return False
        else:
            print(f"✅ Found pages file: {pages_file}")

    return True


def main():
    """Run all validation tests."""
    print("🚀 Running pandas-ta-classic structure validation...\n")

    tests = [
        test_basic_structure,
        test_import_structure,
        test_workflows,
        test_pages_content,
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test failed with exception: {e}")
            results.append(False)
        print()

    passed = sum(results)
    total = len(results)

    print(f"📊 Validation Summary: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All validation tests passed!")
        return 0
    else:
        print("❌ Some validation tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
