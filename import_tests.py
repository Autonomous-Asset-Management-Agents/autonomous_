import os
import sys


def main():
    import tests.conftest

    for root, dirs, files in os.walk("tests/unit"):
        for file in files:
            if file.startswith("test_") and file.endswith(".py"):
                path = os.path.join(root, file)
                mod_name = path.replace("\\", ".").replace("/", ".")[:-3]
                print(f"Importing {mod_name}...")
                sys.stdout.flush()
                try:
                    __import__(mod_name)
                except BaseException as e:
                    print(f"Error importing {mod_name}: {e}")
                print(f"Done importing {mod_name}")
                sys.stdout.flush()


if __name__ == "__main__":
    main()
