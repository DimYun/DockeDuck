import sys
import pandas as pd


def main():
    print(f"DockDuck Environment Initialized.")
    print(f"Python Version: {sys.version}")

    df = pd.DataFrame({"Status": ["Ready", "Isolated", "Non-Root"]})
    print("\nSystem Check:\n", df)


if __name__ == "__main__":
    main()