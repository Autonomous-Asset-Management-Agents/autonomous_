import re
import sys

# We remove anything that matches these prefixes
HEAVY_PREFIXES = (
    "torch",
    "torchvision",
    "torchaudio",
    "stable-baselines3",
    "sb3-contrib",
    "PyQt6",
    "pyqt6-charts",
)


def filter_requirements(lines: list[str]) -> list[str]:
    """
    Filters out heavy ML/GUI dependencies for the CI environment.
    """
    filtered_lines = []
    for line in lines:
        stripped = line.strip()
        # Keep empty lines and comments unconditionally
        if not stripped or stripped.startswith("#"):
            filtered_lines.append(line)
            continue

        # Parse the package name (everything before ==, >=, <=, etc.)
        match = re.match(r"^([a-zA-Z0-9_\-]+)", stripped)
        if match:
            pkg_name = match.group(1)
            # If the package name starts with any of our targeted heavy prefixes, skip it
            # Using exact match or prefix match (e.g. PyQt6 matches PyQt6, PyQt6-Charts)
            if any(pkg_name.startswith(p) for p in HEAVY_PREFIXES):
                continue

        filtered_lines.append(line)

    return filtered_lines


def main():
    if len(sys.argv) < 3:
        print("Usage: python sync_ci_requirements.py <input.txt> <output.txt>")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    with open(input_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    filtered = filter_requirements(lines)

    with open(output_file, "w", encoding="utf-8") as f:
        f.writelines(filtered)

    print(f"Successfully generated {output_file} ({len(filtered)} lines).")


if __name__ == "__main__":
    main()
