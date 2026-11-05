"""MBPP evaluator entry point; task execution awaits the restricted sandbox."""

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent Smith MBPP evaluator (scaffold only)")
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--provider-url", required=True)
    parser.parse_args()
    parser.exit(2, "MBPP task execution is not implemented: restricted sandbox required.\n")


if __name__ == "__main__":
    raise SystemExit(main())
